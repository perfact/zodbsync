import logging
HAS_SYSTEMD = False
try:
    import systemd.journal
    try:
        logging_handler = systemd.journal.JournalHandler
    except AttributeError:
        # structure of module changed? check debian-packages?
        logging_handler = systemd.journal.JournaldLogHandler
    HAS_SYSTEMD = True
except ImportError:
    # Fall back to a standard syslog handler
    import logging.handlers
    logging_handler = logging.handlers.SysLogHandler

def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if HAS_SYSTEMD:
        logger.addHandler(logging_handler(SYSLOG_IDENTIFIER=name))
    else: logger.addHandler(logging_handler())
    logger.addHandler(logging.StreamHandler())
    return logger
