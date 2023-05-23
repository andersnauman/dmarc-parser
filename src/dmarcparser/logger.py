#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Logger """

import logging
import sys

SYSLOG_TO_FILE = 1 << 0
SYSLOG_TO_SCREEN = 1 << 1

def _custom_logger(name, debug_level=logging.INFO, handler=SYSLOG_TO_SCREEN):
    """
    Create a custom logger instead of modifing the core logger
    https://stackoverflow.com/questions/28330317/print-timestamp-for-logging-in-python
    """
    formatter = logging.Formatter(fmt='%(asctime)s %(levelname)-8s %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')

    logger = logging.getLogger(name)
    logger.setLevel(debug_level)

    if handler & SYSLOG_TO_FILE:
        file_handler = logging.FileHandler('log.txt', mode='w')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if handler & SYSLOG_TO_SCREEN:
        screen_handler = logging.StreamHandler(stream=sys.stdout)
        screen_handler.setFormatter(formatter)
        logger.addHandler(screen_handler)

    return logger
