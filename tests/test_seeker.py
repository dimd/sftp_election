import unittest

import etcd3
import mock

from sftp_election.seeker import LeaseUpdater, Seeker, SupervisordMgr


class SeekerTest(unittest.TestCase):

    @mock.patch('sftp_election.seeker.create_logger')
    @mock.patch('sftp_election.seeker.logger')
    def setUp(self, logger_mock, create_logger_mock):
        def dummy_true(cls):
            return True

        def dummy_false(cls):
            return False

        Seeker.dummy_true = dummy_true
        Seeker.dummy_false = dummy_false
        self.seeker = Seeker({'': True}, '')
        self.seeker._etcd_client = mock.Mock(spec=etcd3.Etcd3Client)
        self.seeker._etcd_client.transactions = mock.Mock()

    @mock.patch('sftp_election.seeker.etcd3')
    @mock.patch('sftp_election.seeker.config')
    def test_etcd_client(self, config_mock, etcd_mock):
        config_mock.app_etcd_uri = 'foo.bar:1000'
        with mock.patch('sftp_election.seeker.create_logger'), \
                mock.patch('sftp_election.seeker.logger'):
            s = Seeker({'': True}, '')
            s.etcd_client

    @mock.patch('sftp_election.seeker.etcd3')
    @mock.patch('sftp_election.seeker.config')
    def test_etcd_client_deleter(self, config_mock, etcd_mock):
        config_mock.app_etcd_uri = 'foo.bar:1000'
        with mock.patch('sftp_election.seeker.create_logger'), \
                mock.patch('sftp_election.seeker.logger'):
            s = Seeker({'': True}, '')
            s.etcd_client
            del s.etcd_client
            assert s._etcd_client is None

    def test_invalid_state(self):
        with self.assertRaises(ValueError):
            Seeker({}, '')

    def test_set_state(self):
        self.seeker.set_state('test')

        self.assertEqual(
            self.seeker.state,
            'test'
        )

    def test_run_state_action_result_true(self):
        self.seeker.transitions = {
            '': {
                'action': 'dummy_true',
                'next_true': 'true_state',
            }
        }

        self.seeker.run_state_action()

        self.assertEqual(
            self.seeker.state,
            'true_state'
        )

    def test_run_state_action_result_false(self):
        self.seeker.transitions = {
            '': {
                'action': 'dummy_false',
                'next_false': 'false_state',
            }
        }

        self.seeker.run_state_action()

        self.assertEqual(
            self.seeker.state,
            'false_state'
        )

    @mock.patch('sftp_election.seeker.config')
    def test_is_charging_env(self, config_mock):
        config_mock.ftp_role = 'ccf'

        self.assertTrue(self.seeker.is_charging_env())

    @mock.patch('sftp_election.seeker.config')
    def test_is_not_a_charging_env(self, config_mock):
        config_mock.storage_dir = False

        self.assertFalse(self.seeker.is_charging_env())

    def test_trigger_legacy(self):
        assert self.seeker.trigger_legacy() is True

        self.seeker.logger.info.assert_called_once_with(
            'start the server'
        )

    def test_trigger_legacy_exception(self):
        self.seeker.logger.info.side_effect = [Exception, mock.DEFAULT]

        assert self.seeker.trigger_legacy() is False

        self.seeker.logger.info.assert_called_with(
            'unexpected exception caught: '
        )

    @mock.patch('sftp_election.seeker.config', autospec=True)
    def test__get_placeholder_entries(self, config_mock):
        config_mock.app_etcd_prefix = 'test'

        foo_key = (
            '{0}'
            '/services'
            '/ftpsftp-server'
            '/foo-placeholder/test-key'
        ).format(config_mock.app_etcd_prefix)

        etcd_result = (
            (None, mock.Mock(key=foo_key)),
        )

        self.seeker.etcd_client.get_prefix.return_value = etcd_result

        self.assertEqual(
            self.seeker._get_placeholder_entries(),
            set([foo_key.rsplit('/', 1)[0]])
        )

    def test__get_placeholder_entries_exception(self):
        self.seeker.etcd_client.get_prefix.side_effect = \
            etcd3.exceptions.Etcd3Exception

        self.assertEqual(
            self.seeker._get_placeholder_entries(),
            []
        )

    @mock.patch('sftp_election.seeker.SupervisordMgr', spec=SupervisordMgr)
    @mock.patch('sftp_election.seeker.config')
    def test__start_server(self, config_mock, supervisor_mock):
        config_mock.ftp_mode = 'sftp'
        self.seeker._start_server()

        expected_calls = [
            mock.call('keycheck'),
            mock.call().reload(),
            mock.call('sshd-conf-manager'),
            mock.call().reload(),
        ]

        assert supervisor_mock.mock_calls == expected_calls

        supervisor_mock.reset_mock()

        config_mock.ftp_mode = 'ftp'
        self.seeker._start_server()

        expected_calls = [
            mock.call('vsftpd'),
            mock.call().reload(),
        ]

        assert supervisor_mock.mock_calls == expected_calls

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    @mock.patch('sftp_election.seeker.config')
    def test__stop_server(self, config_mock, supervisor_mock):
        config_mock.ftp_mode = 'sftp'
        self.seeker._stop_server()

        expected_calls = [
            mock.call('sshd'),
            mock.call().stop(),
            mock.call('sshd-conf-manager'),
            mock.call().stop(),
            mock.call('keycheck'),
            mock.call().stop(),
        ]

        assert supervisor_mock.mock_calls == expected_calls

        supervisor_mock.reset_mock()

        config_mock.ftp_mode = 'ftp'
        self.seeker._stop_server()

        expected_calls = [
            mock.call('vsftpd'),
            mock.call().stop(),
        ]

        assert supervisor_mock.mock_calls == expected_calls

    def test__entry_has_owner(self):
        self.seeker.etcd_client.get.return_value = \
            ('foo', None,)
        self.assertTrue(self.seeker._entry_has_owner(''))

    def test__entry_has_owner_exception(self):
        self.seeker.etcd_client.get.return_value = \
            (None, None,)

        self.assertFalse(self.seeker._entry_has_owner(''))

    def test__get_entry_deployment_status(self):
        self.seeker.etcd_client.get.return_value = ('test', None)

        self.assertEqual(
            self.seeker._get_entry_deployment_status(''),
            'test'
        )

    def test__get_entry_deployment_status_exception(self):
        self.seeker.etcd_client.get.side_effect = \
            etcd3.exceptions.Etcd3Exception

        self.assertEqual(
            self.seeker._get_entry_deployment_status(''),
            ''
        )

    @mock.patch('sftp_election.seeker.get_container_id')
    @mock.patch('sftp_election.seeker.get_hostname')
    @mock.patch('sftp_election.seeker.config', autospec=True)
    @mock.patch('sftp_election.seeker.subprocess', autospec=True)
    def test__elect_myself_exception(self,
                                     subprocess_mock,
                                     config_mock,
                                     hostname_mock,
                                     container_mock):
        config_mock.ftp_mode = 'ftp'
        config_mock.server_config_file = 'test'
        subprocess_mock.check_output.return_value = ''
        self.seeker.etcd_client.get.return_value = ('foo',)
        self.seeker.etcd_client.transaction.side_effect = \
            etcd3.exceptions.Etcd3Exception
        self.seeker.etcd_client.replace.side_effect = \
            etcd3.exceptions.Etcd3Exception

        with mock.patch('__builtin__.open', mock.mock_open(), create=True):
            self.assertIsNone(self.seeker._elect_myself(''))

    @mock.patch('sftp_election.seeker.get_container_id')
    @mock.patch('sftp_election.seeker.get_hostname')
    @mock.patch('sftp_election.seeker.config', autospec=True)
    @mock.patch('sftp_election.seeker.subprocess', autospec=True)
    def test__elect_myself(self,
                           subprocess_mock,
                           config_mock,
                           hostname_mock,
                           container_mock):
        hostname_mock.return_value = 'hostname'
        container_mock.return_value = '1234'
        config_mock.ftp_mode = 'sftp'
        config_mock.vnf_name = 'foo01'
        config_mock.skydns_domain = 'local'
        config_mock.server_config_file = 'test'
        subprocess_mock.check_output.return_value = ''
        self.seeker.etcd_client.get.return_value = ('foo',)
        self.seeker.etcd_client.transaction.return_value = \
            ('bar', None,)
        self.seeker.etcd_client.lease.return_value = mock.sentinel

        with mock.patch('__builtin__.open',
                        mock.mock_open(read_data='ForceCommand\n'),
                        create=True):
            self.seeker._elect_myself('/radio/head')

        expected_calls = [
            mock.call('/radio/head/owner', 'hostname/1234',
                      lease=mock.sentinel),
            mock.call('/skydns/local/foo01/ssh-proxy/head/hostname/1234',
                      '{"host": "", "port": 22}',
                      lease=mock.sentinel
                      ),
        ]
        assert self.seeker.etcd_client.transactions.put.mock_calls == \
            expected_calls
        assert self.seeker.etcd_client.replace.mock_calls == [
             mock.call('/radio/head/deployment-status', 'deployed', 'start')
        ]
        assert self.seeker.elected_entry == '/radio/head'

    @mock.patch('sftp_election.seeker.get_container_id')
    @mock.patch('sftp_election.seeker.get_hostname')
    @mock.patch('sftp_election.seeker.SupervisordMgr')
    @mock.patch('sftp_election.seeker.config')
    @mock.patch('sftp_election.seeker.subprocess')
    def test_discover_an_entry_in_etcd(self,
                                       subprocess_mock,
                                       config_mock,
                                       supervisor_mock,
                                       hostname_mock,
                                       container_mock):
        config_mock.ftp_mode = 'sftp'
        config_mock.server_config_file = 'test'
        config_mock.app_etcd_prefix = 'foo'
        subprocess_mock.check_output.return_value = ''
        self.seeker.etcd_client.get_prefix.return_value = \
            (
                (None, mock.Mock(key='foo/services/ftpsftp-server/bar/'),),
            )
        self.seeker.etcd_client.get.side_effect = [
            # _get_entry_deployment_status
            ('start', None,),
            # _entry_has_owner
            (None, None,),
            # _check_root_path
            ('', None,),
            # _set_user_dir
            ('foo', None,)
        ]

        self.seeker.etcd_client.transaction.return_value = \
            (True, None,)

        with mock.patch('__builtin__.open', mock.mock_open(), create=True):
            assert self.seeker.discover_an_entry_in_etcd()

    @mock.patch('sftp_election.seeker.time', autospec=True)
    def test_no_discovery_an_entry_in_etcd(self, time_mock):
        self.seeker.etcd_client.get_prefix.return_value = []

        self.seeker.discover_an_entry_in_etcd()

        time_mock.sleep.assert_called_with(1)

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    def test_watch_etcd_deployment_status_stop(self, supervisor_mock):
        self.seeker.elected_entry = ''
        etcd_results = [
            mock.Mock(value='stop', key='/deployment-status'),
            mock.Mock(value=None)
        ]

        self.seeker.etcd_client.watch_prefix.return_value = (
            etcd_results,
            mock.Mock()
        )
        assert self.seeker.watch_etcd() is False

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    def test_watch_etcd_delete_deployemnt_status(self, supervisor_mock):
        self.seeker.elected_entry = ''
        etcd_results = [
            mock.Mock(value='foo',
                      key='/deployment-status',
                      spec=etcd3.events.DeleteEvent),
            mock.Mock(value=None)
        ]
        self.seeker.etcd_client.watch_prefix.return_value = (
            etcd_results,
            mock.Mock()
        )

        assert self.seeker.watch_etcd() is False
        assert self.seeker.logger.debug.mock_calls == [
            mock.call('deployment-status key was deleted'),
            mock.call('deleting the owner key'),
        ]

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    def test_watch_etcd_change_root_path_key(self, supervisor_mock):
        self.seeker.elected_entry = ''
        etcd_results = [
            mock.Mock(value='foo',
                      key='/root-path',
                      spec=etcd3.events.PutEvent),
            mock.Mock(value=None)
        ]
        self.seeker.etcd_client.watch_prefix.return_value = (
            etcd_results,
            mock.Mock()
        )

        assert self.seeker.watch_etcd() is False
        assert self.seeker.logger.debug.mock_calls == [
            mock.call('root-path key changed'),
            mock.call('deleting the owner key'),
        ]

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    def test_watch_etcd_exception(self, supervisor_mock):
        event_mock = mock.MagicMock()
        type(event_mock).key = mock.PropertyMock(
            side_effect=etcd3.exceptions.Etcd3Exception)
        etcd_results = [
            event_mock
        ]

        self.seeker.etcd_client.watch_prefix.return_value = (
            etcd_results,
            mock.Mock()
        )
        assert self.seeker.watch_etcd() is False
        assert self.seeker.logger.info.mock_calls == [
            mock.call('etcd connection is down'),
            mock.call('stop the server'),
        ]

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    def test__clean_up_etcd_exception(self, supervisor_mock):
        self.seeker.etcd_client.delete.side_effect = \
            etcd3.exceptions.Etcd3Exception

        self.seeker._clean_up()
        assert self.seeker.logger.debug.mock_calls == [
            mock.call('deleting the owner key'),
            mock.call('cannot clean etcd keys, because of connectivity '
                      'issues with etcd'),
        ]

    @mock.patch('sftp_election.seeker.SupervisordMgr')
    @mock.patch('sftp_election.seeker.sys')
    def test__clean_up_sgnal_exit(self, sys_mock, supervisor_mock):
        self.seeker._clean_up(signum=1)

        assert self.seeker.logger.debug.mock_calls == [
            mock.call('deleting the owner key'),
            mock.call('Received signal: 1'),
        ]
        sys_mock.exit.assert_called_once_with(0)

    @mock.patch('sftp_election.seeker.sys')
    def test_exit(self, sys_mock):
        self.seeker.exit()

        sys_mock.exit.assert_called_once_with(0)

    def test_lease_updater(self):
        l = LeaseUpdater(mock.Mock())
        assert l.is_alive() is True
        l.event.set()
        l.join()
        assert l.is_alive() is False

    @mock.patch('sftp_election.seeker.time')
    def test_lease_updater_exception(self, time_mock):
        time_mock.sleep.side_effect = etcd3.exceptions.Etcd3Exception
        l = LeaseUpdater(mock.Mock())
        l.join()
        assert l.is_alive() is False

    def test_lease(self):
        self.seeker.etcd_client.lease.return_value = mock.sentinel
        assert self.seeker.lease is mock.sentinel

    def test_del_lease(self):
        lease_mock = mock.Mock(spec=etcd3.Lease)
        self.seeker.etcd_client.lease.return_value = lease_mock
        self.seeker.lease
        del self.seeker.lease

        lease_mock.revoke.assert_called_once()
        assert self.seeker._lease is None

    def test__get_host(self):
        self.seeker.etcd_client.get_prefix.return_value = (
            ('foo/abc', mock.Mock(key='test/value')),
            ('bar', mock.Mock(key='')),
        )

        assert list(self.seeker._get_host('test/value')) == ['foo']

    @mock.patch('sftp_election.seeker.get_hostname')
    def test__same_host(self, get_hostname_mock):
        self.seeker.etcd_client.get_prefix.return_value = (
            ('foo.bar/123', mock.Mock(key='b/a/owner')),
            ('bar', mock.Mock(key='')),
        )
        get_hostname_mock.return_value = 'foo.bar'

        assert self.seeker._same_host('a/b')

    @mock.patch('sftp_election.seeker.get_hostname')
    def test__not_same_host(self, get_hostname_mock):
        self.seeker.etcd_client.get_prefix.return_value = (
            ('foo.bar/123', mock.Mock(key='c/a/owner')),
            ('bar', mock.Mock(key='')),
        )
        get_hostname_mock.return_value = 'foo.bar'

        assert self.seeker._same_host('a/b') is False

    @mock.patch('sftp_election.seeker.get_hostname')
    def test__check_root_path_ccf_and_cdr(self, get_hostname_mock):
        self.seeker.etcd_client.get.return_value = ('ccf-123/cdr-group-001',
                                                    None,)
        self.seeker.etcd_client.get_prefix.return_value = (
            ('foo.bar/123', mock.Mock(key='cdr-group-001/ccf-123/owner')),
        )
        get_hostname_mock.return_value = 'foo.bar'

        assert self.seeker._check_root_path('doot')

    @mock.patch('sftp_election.seeker.get_hostname')
    def test__check_root_path_only_ccf(self, get_hostname_mock):
        self.seeker.etcd_client.get.return_value = ('ccf-123', None,)
        self.seeker.etcd_client.get_prefix.return_value = (
            ('foo.bar/123', mock.Mock(key='cdr-group-000/ccf-123/owner')),
        )
        get_hostname_mock.return_value = 'foo.bar'

        assert self.seeker._check_root_path('doot')

    def test__check_root_path_no_match(self):
        self.seeker.etcd_client.get.return_value = ('random_value', None,)

        assert self.seeker._check_root_path('foo') is False

    def test__check_root_path_exception(self):
        self.seeker.etcd_client.get.side_effect = \
            etcd3.exceptions.Etcd3Exception

        self.seeker._check_root_path('foo')

        self.seeker.logger.debug.assert_called_once()

    @mock.patch('sftp_election.seeker.config')
    def test__set_user_dir_empty_ch_dir(self, config_mock):
        config_mock.ftp_mode = 'sftp'
        with mock.patch('__builtin__.open',
                        mock.mock_open(read_data='ForceCommand\n'),
                        create=True):
            self.seeker.etcd_client.get.return_value = ('', None,)
            self.seeker._set_user_dir('')
