from setuptools import find_packages, setup

from sftp_election import __version__

setup(
    name='sftp_election',
    version=__version__,
    description='Elect sftp server for ccf',
    packages=find_packages(include=['sftp_election']),
    author='Dimitris Dalianis',
    author_email='dalianis.dimitris@nokia.com',
    install_requires=[
        'etcd3>0.6',
        'supervisor'
    ],
    entry_points={
        'console_scripts': [
            'sftp-election = sftp_election.runner:run',
        ]
    },
    keywords=['etcdv3', 'sftp', 'ftp'],
    url='',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 2.7',
    ],
)
