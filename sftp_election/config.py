"""Configuration module."""
import os


app_etcd_uri = os.getenv('APP_ETCD_URI')
app_etcd_prefix = os.getenv('APP_ETCD_PREFIX')
ftp_mode = os.getenv('FTP_MODE')
ftp_role = os.getenv('FTP_ROLE')
skydns_domain = os.getenv('SKYDNS_DOMAIN')
vnf_name = os.getenv('VNF_NAME')

if ftp_mode == 'ftp':
    server_config_file = '/etc/vsftpd/vsftpd.conf'
elif ftp_mode == 'sftp':
    server_config_file = '/etc/ssh/sshd_config'
