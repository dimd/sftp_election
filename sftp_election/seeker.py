"""sftp_election.seeker module.

This module provides the Seeker object which is responsible for discovering
an etcd entry for the (s)ftp container, writing itself into one and wathcing
for changes.
"""
import json
import logging  # TODO remove
import re
import signal
import subprocess
import sys
import threading
import time
import urlparse

from collections import OrderedDict

import etcd3
from etcd3.exceptions import Etcd3Exception

from lb_lib_py.logger import create_logger, logger
from lb_lib_py.supervisord_mgr import SupervisordMgr
from lb_lib_py.utils import get_container_id, get_hostname

import config


class LeaseUpdater(threading.Thread):
    """Update an etcd lease in a seperate thread.

    Attributes:
        event: Event object to communicate between threads
        lease: An etcd lease object

    """

    def __init__(self, lease):
        """Initialize a daemon thread and start it.

        Start method executes the run method. So, upon object creation the
        thread executes its action.
        Daemon mode means that the thread will terminate when all the
        non-daemon threads (including the main thread) terminate.
        """
        threading.Thread.__init__(self)
        self.lease = lease
        self.event = threading.Event()
        self.daemon = True
        self.start()

    def run(self):
        """Thread's action. Refresh the lease.

        When the main thread sets the event flag the thread is terminated
        """
        try:
            while not self.event.is_set():
                self.lease.refresh()
                time.sleep(3)
        except Etcd3Exception:
            logger.info(
                'Lease thread will terminate because of'
                ' etcd connection failure'
            )


class Seeker(object):
    """Find a proper place in etcd.

    Proper place is a predefined entry that has been prepared by another
    container/service. The seeker has a set of predefined states that it
    will be in at any given time.

    Attributes:
        transitions: A dict of dicts containing all the possible state
            transitions and associated actions.
        init_state: A string representing the current state.
        elected_entry: A string representing the etcd directory that we
            have written ourselves in.

    """

    def __init__(self, transitions, init_state):
        """Seeker constructor.

        Args:
            transitions(dict): A dict of dicts containing all the possible
                state transitions.
            init_state(str): A string indicating the initial state. Must be a
                valid state.

        Raises:
            ValueError: If the init_state is not a key in the transitions
                dict.

        Example:
            >>> state1 = { 'action': 'foo',
            ...            'next_true': 'foo_state',
            ...            'next_false':'bar_state'
            ...          }
            >>> state2 = { 'action': 'foo_state',
            ...            'next_true': 'foo_state',
            ...          }

            >>> transitions = {'init_state': state1, 'foo_state': state2}
            >>> Seeker(transitions, 'init_state')

        """
        self._etcd_client = None
        self._lease = None
        self.elected_entry = None
        self.lease_updater = None
        self.transitions = transitions

        if not self.transitions.get(init_state):
            raise ValueError('Not a valid state: '.format(init_state))
        else:
            self.state = init_state
        create_logger()
        self.logger = logger
        self.logger.setLevel(logging.DEBUG)  # TODO remove

        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT,):
            signal.signal(sig, self._clean_up)

    @property
    def etcd_client(self):
        """Lazy initialization of the etcd client. Not all states need one."""
        if not self._etcd_client:
            etcd_host = urlparse.urlparse(config.app_etcd_uri)
            self._etcd_client = etcd3.client(host=etcd_host.hostname,
                                             port=etcd_host.port,
                                             timeout=3)

        return self._etcd_client

    @etcd_client.deleter
    def etcd_client(self):
        self._etcd_client = None

    @property
    def lease(self):
        """Get leases."""
        if not self._lease:
            self._lease = self.etcd_client.lease(10)
        return self._lease

    @lease.deleter
    def lease(self):
        self._lease.revoke()
        self._lease = None

    def is_charging_env(self):
        """Decide if the (s)ftp server should be used by the charging containers.

        Returns
            bool: True if we are in a charging env, False otherwise.

        """
        if config.ftp_role in ['ccf', 'cha-ccf']:
            self.logger.info('I am a charging ftp/sftp server')
            return True
        else:
            self.logger.info('I am not a charging ftp/sftp server')
            return False

    def _start_server(self):
        self.logger.info('start the server')
        if config.ftp_mode == 'sftp':
            SupervisordMgr('keycheck').reload()
            SupervisordMgr('sshd-conf-manager').reload()
        elif config.ftp_mode == 'ftp':
            SupervisordMgr('ftp-configurator').reload()
            SupervisordMgr('vsftpd').reload()

    def _stop_server(self):
        self.logger.info('stop the server')
        if config.ftp_mode == 'sftp':
            SupervisordMgr('sshd').stop()
            SupervisordMgr('sshd-conf-manager').stop()
            SupervisordMgr('keycheck').stop()
        elif config.ftp_mode == 'ftp':
            SupervisordMgr('vsftpd').stop()
            SupervisordMgr('ftp-configurator').stop()

    def trigger_legacy(self):
        """Start the (s)ftp server in legacy mode.

        Legacy mode ignores the whole etcd placeholder logic. It just starts
        the (s)ftp server which will always run and wait for connections.

        Returns
            bool: True if the server starts succesfully, False otherwise.

        """
        try:
            self._start_server()
            return True
        except Exception as e:
            self.logger.info('unexpected exception caught: {0}'.format(e))
            return False

    def _get_placeholder_entries(self):
        """Get available directories under a pre-defined path.

        These entries will be created by the charging container.

        Returns:
            list: A list of the placeholder entries found in etcd.

        """
        placeholder_dir = ('{0}'
                           '/services'
                           '/ftpsftp-server').format(config.app_etcd_prefix)
        subdirs_regex = re.compile(r'({0}/[\w-]+)/.*'.format(placeholder_dir))
        try:
            placeholder_keys = set()
            etcd_result = self.etcd_client.get_prefix(placeholder_dir)

            for _, metadata in etcd_result:
                m = subdirs_regex.match(metadata.key)
                if m:
                    placeholder_keys.add(m.group(1))

            self.logger.info('available placeholders: {0}'.format(
                placeholder_keys))

            return placeholder_keys
        except Etcd3Exception as e:
            self.logger.info('no available placeholders {0}'.format(e))
            return []

    def _entry_has_owner(self, entry):
        """Check if a placeholder entry has an owner key.

        Args:
            entry (str): An etcd path.

        Returns:
            bool: Whether the particular entry has a key named `owner`.

        """
        owner_entry = '{0}/owner'.format(entry)
        owner_value, _ = self.etcd_client.get(owner_entry)
        if owner_value:
            self.logger.debug('entry {0} has an owner key'.format(entry))
            return True
        self.logger.debug(
            'entry {0}, doesn\'t have an owner key'.format(
                entry
            )
        )
        return False

    def _get_entry_deployment_status(self, entry):
        """Return the value of the deployment key.

        Args:
          entry (str): An etcd path.

        Returns:
            str: Value of the deployment status.

        """
        deployment_status_entry = '{0}/deployment-status'.format(entry)
        self.logger.debug(deployment_status_entry)
        try:
            deployment_status, _ = self.etcd_client.get(
                                    deployment_status_entry)
            self.logger.debug('deployement status is: {0}'.format(
                deployment_status))

            return deployment_status
        except Etcd3Exception as e:
            self.logger.debug('unexpected error. '
                              'deployement status could not be read. '
                              'Entry: {0} '
                              'Exception caught: {1}'.format(entry, e))

            return ''

    @staticmethod
    def get_skydns_hostname():
        hostname = get_hostname()

        return '/'.join(
            reversed(hostname.split('.'))
        )

    @staticmethod
    def get_proxy_info():
        if config.ftp_mode == 'sftp':
            return 'ssh', 22
        elif config.ftp_mode == 'ftp':
            return 'ftp', 21

    def _elect_myself(self, entry):
        """Fill the owner key and set the server cd dir.

        The owner key value is <OWN HOSTNAME>/<OWN CONTAINER ID>.

        Args:
          entry (str): An etcd path.
        """
        owner_entry = '{0}/owner'.format(entry)
        owner_value = '{0}/{1}'.format(get_hostname(), get_container_id()[:12])
        deployment_status_entry = '{0}/deployment-status'.format(entry)
        proxy_type, proxy_port = self.get_proxy_info()
        proxy_entry = '/skydns/{0}/{1}/{2}-proxy/{3}/{4}/{5}'.format(
            config.skydns_domain,
            config.vnf_name,
            proxy_type,
            entry.split('/')[-1],
            self.get_skydns_hostname(),
            get_container_id()[:12]
        )
        proxy_value = json.dumps(
            OrderedDict([
                ('host',
                 subprocess.check_output(
                     ['get-own-ip-address', 'oam']
                 ).strip()),
                ('port', proxy_port),
            ])
        )
        try:
            self._set_user_dir(entry)
            status, _ = self.etcd_client.transaction(
                compare=[
                    self.etcd_client.transactions.version(owner_entry)
                    == 0
                ],
                success=[
                    self.etcd_client.transactions.put(owner_entry,
                                                      owner_value,
                                                      lease=self.lease),
                    self.etcd_client.transactions.put(proxy_entry,
                                                      proxy_value,
                                                      lease=self.lease),
                ],
                failure=[]
            )
            self.etcd_client.replace(deployment_status_entry,
                                     'deployed',
                                     'start')
            self.elected_entry = entry
            self.lease_updater = LeaseUpdater(self.lease)
        except Etcd3Exception as e:
            self.logger.debug('Election went wrong in {0}. '
                              'Reason: {1}'.format(entry, e))

    @staticmethod
    def _replace_or_add_line(line, config_file, regexp_obj):

        # Read the configuration file contents
        with open(config_file, 'r') as f:
            conf = f.read()

        if regexp_obj.search(conf):
            # If a previous value is there, replace it
            with open(config_file, 'w') as f:
                f.write(regexp_obj.sub(line, conf))
        else:
            # else the line is not there, so add (append) it
            with open(config_file, 'a') as f:
                f.write(line)

    def _write_ch_dir(self, ch_dir):
        if config.ftp_mode == 'ftp':
            regex = re.compile(r'local_root.*\n')
            new_ch_dir = 'local_root={0}\n'.format(ch_dir)
        elif config.ftp_mode == 'sftp':
            regex = re.compile(r'ForceCommand.*\n')
            new_ch_dir = 'ForceCommand internal-sftp -d {0}\n'.format(ch_dir)

        self._replace_or_add_line(new_ch_dir,
                                  config.server_config_file,
                                  regex)

    def _set_user_dir(self, entry):
        """Set the directory the logged in user will chdir.

        Args:
            entry (str): An etcd path.

        """
        ch_dir_key = '{0}/root-path'.format(entry)
        ch_dir = self.etcd_client.get(ch_dir_key)[0]
        if not ch_dir:
            ch_dir = '.'
        self._write_ch_dir(ch_dir)

    def _get_host(self, owner_suffix):
        cdr_stream_key = '/tas01/services/charging/cdr-streams'
        for value, metadata in self.etcd_client.get_prefix(cdr_stream_key):
            if metadata.key.endswith(owner_suffix):
                yield value.split('/')[0]

    def _same_host(self, root_path):
        my_host = get_hostname()

        reverse_root_path = list(reversed(root_path.split('/')))
        reverse_root_path.append('owner')
        cdr_stream_owner_suffix = '/'.join(reverse_root_path)

        for cdr_stream_host in self._get_host(cdr_stream_owner_suffix):
            if my_host == cdr_stream_host:
                self.logger.debug('Going to serve {0}'.format(root_path))
                return True

        return False

    def _check_root_path(self, entry):
        """Check if the root path key fulfils the criteria.

        The criteria are:
            - The root_path key is the empty string ""
            - The key has the pattern ccf-<xxx> and we are on the same host
            - The key has the pattern ccf-<xxx>/cdr-group-<yyy>
              and we are on the same host

        Returns:
            bool: Whether root path key is ok

        """
        root_path_key = '{0}/root-path'.format(entry)

        try:
            value, _ = self.etcd_client.get(root_path_key)

            if not value:
                self.logger.debug(
                    'root-path key is empty. Going to serve all cdr streams'
                    ' of all ccfs'
                )
                return True
            elif re.match(r'ccf-\d{3}(?=$|/cdr-group-\d{3}$)', value):
                return self._same_host(value)

            return False
        except Etcd3Exception:
            self.logger.debug(
                'Could not get root-path key for {0}'.format(entry)
            )

    def discover_an_entry_in_etcd(self):
        """Look for a placeholder entry in etcd.

        The criteria for discovery are:
            - Iterate over all available directories under a predefined etcd
              path.
            - For each entry test if there is not an owner key and the
              deployment status is `deployed` or `start`.
            - The root-path key abides to certain host rules.
            - Write the container info in the owner key.

        If any of the above steps fail, the whole discovery is considered
        failed. A delay is introduced after each failed discovery
        attempt. The reason behind this is that, most probably, a new discovery
        phase will be attempted asap.

        Returns:
            bool: Whether the discovery has succeeded.

        """
        valid_states = ['deployed', 'start']
        for entry in self._get_placeholder_entries():
            if (
                self._get_entry_deployment_status(entry) in valid_states
                and not self._entry_has_owner(entry)
                and self._check_root_path(entry)
            ):
                self._elect_myself(entry)
                if self.elected_entry:
                    self._start_server()
                    self.logger.info('Found a spot: {0}'.format(
                        self.elected_entry))
                    return True
        time.sleep(1)
        return False

    def watch_in_a_thread(self):
        """Run the watch_etcd function in a thread.

        The watch_etcd function blocks the main thread if called directly.
        This way the signal handlers already established in the constructor
        are never called when a signal is received.

        """
        t = threading.Thread(target=self.watch_etcd)
        t.daemon = True
        t.start()
        while t.is_alive():
            time.sleep(1)

    def watch_etcd(self):
        """Watch the deployment status of the elected etcd entry.

        If the deployment-status key is set to stop or is deleted or the
        root-path key is changed then delete the owner key, stop the file
        server and re-enter discovery state.
        """
        deployment_status_key = '{0}/deployment-status'.format(
            self.elected_entry)
        root_path_key = '{0}/root-path'.format(self.elected_entry)

        events, cancel = self.etcd_client.watch_prefix(
            self.elected_entry)

        try:
            for e in events:
                if (e.key == deployment_status_key):
                    if e.value == 'stop':
                        self.logger.debug(
                            'Deployment status was changed to \'stop\', '
                            'setting deployment-status key to \'stop-empty\'')
                        self.etcd_client.put(
                            deployment_status_key,
                            'stop-empty'
                        )
                        break
                    elif isinstance(e, etcd3.events.DeleteEvent):
                        self.logger.debug('deployment-status key was deleted')
                        break
                elif (isinstance(e, etcd3.events.PutEvent)
                      and e.key == root_path_key):
                        self.logger.debug('root-path key changed')
                        break
        except Etcd3Exception:
            self.logger.info('etcd connection is down')

        self._clean_up()
        cancel()
        del self.etcd_client
        return False

    def _clean_up(self, signum=None, stack_frame=None):
        try:
            self.logger.debug(
                'deleting the owner key')
            self.etcd_client.delete(
                '{0}/owner'.format(self.elected_entry))
            if self.lease_updater:
                self.lease_updater.event.set()
            if self.lease:
                del self.lease
        except Etcd3Exception:
            self.logger.debug('cannot clean etcd keys, '
                              'because of connectivity '
                              'issues with etcd')

        self._stop_server()
        self.elected_entry = None
        if signum:
            self.logger.debug('Received signal: {0}'.format(signum))
            self.exit()

    def exit(self):
        """Exit."""
        self.logger.info('Exiting')
        sys.exit(0)

    # Really simple state machine
    def set_state(self, next_state):
        """Setter for the state attribute."""
        self.state = next_state
        self.logger.debug('Entering {0} state'.format(self.state))

    def run_state_action(self):
        """Execute the state's action.

        The next state is depending on the outcome of the
        action (True or False). The action is represented by a string.
        A method with that name must exist.

        Raises:
            AttributeError: If the action is not a bound method of the Seeker
                instance.

        """
        transition = self.transitions.get(self.state)
        action = getattr(self, transition.get('action'))

        if action():
            next_state = transition.get('next_true')
        else:
            next_state = transition.get('next_false')

        self.set_state(next_state)
