#!/usr/bin/env python
from setuptools import setup, find_packages

setup(
    name='emusavelib',
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    install_requires=['llfuse==0.40', 'python-daemon'],
    scripts=['bin/ps1mcfs.py']
)
