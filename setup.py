#!/usr/bin/env python

from setuptools import setup

VERSION = '1.0.0'
setup(
    name='gdsyncpy',
    version=VERSION,
    description='A simple command-line tool for deduplicating and syncing local and Google Drive '
                'folders by their MD5 hashes.',
    author='Giuliano Mega',
    author_email='giuliano.mega@gmail.com',
    license='BSD',
    packages=['commands', 'drive', 'commands.sync'],
    package_data={'drive': ['*.json']},
    entry_points={
        'console_scripts': [
            'gdsyncpy = commands.cli:main'
        ]
    },
    python_requires=">=3.5",
    install_requires=[
        'google-api-python-client',
        'oauth2client',
        'marshmallow',
        'python-magic',
        'marshmallow-oneofschema',
        'httplib2shim'
    ]
)
