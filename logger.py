import logging
import sys
import os
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Custom formatter to add colors to log levels"""
    
    # ANSI color codes
    COLORS = {
        'WHITE': '\033[97m',    # White for info
        'GREEN': '\033[92m',    # Green for success/sent
        'RED': '\033[91m',      # Red for errors
        'YELLOW': '\033[93m',   # Yellow for warnings
        'RESET': '\033[0m'      # Reset to default color
    }
    
    def __init__(self, fmt=None, datefmt=None, style='%', validate=True, use_colors=None):
        super().__init__(fmt, datefmt, style, validate)
        self.use_colors = use_colors if use_colors is not None else self._supports_color()
    
    def _supports_color(self):
        """Check if the terminal supports color output"""
        # Check if running on Windows and enable VT support if possible
        if sys.platform.startswith('win'):
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)  # Enable VT processing
                return True
            except:
                pass
        
        # Check if stdout is a TTY and if NO_COLOR environment variable is not set
        return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty() and not bool(os.environ.get('NO_COLOR'))
    
    def format(self, record):
        # Get the original formatted message
        original_msg = record.getMessage()
        
        if self.use_colors:
            # Check for SENT messages (contains [SENT] keyword)
            if '[SENT]' in original_msg or '[PROGRESS]' in original_msg or '[DRY]' in original_msg:
                # Apply green color to the entire message
                colored_msg = f"{self.COLORS['GREEN']}{original_msg}{self.COLORS['RESET']}"
                record.msg = colored_msg
            # Check for ERROR messages
            elif record.levelno == logging.ERROR:
                # Apply red color to the entire message
                colored_msg = f"{self.COLORS['RED']}{original_msg}{self.COLORS['RESET']}"
                record.msg = colored_msg
            # Check for WARNING messages
            elif record.levelno == logging.WARNING:
                # Apply yellow color to the entire message
                colored_msg = f"{self.COLORS['YELLOW']}{original_msg}{self.COLORS['RESET']}"
                record.msg = colored_msg
            # INFO messages (including [INFO] and others) - white color
            elif record.levelno == logging.INFO:
                # Apply white color to the entire message
                colored_msg = f"{self.COLORS['WHITE']}{original_msg}{self.COLORS['RESET']}"
                record.msg = colored_msg
            else:
                # For other cases, just reset colors
                record.msg = f"{self.COLORS['RESET']}{original_msg}{self.COLORS['RESET']}"
        
        # Format the record with the modified message
        formatted = super().format(record)
        
        return formatted


def setup_logger(level: str, log_file: Optional[str] = None):
    logger = logging.getLogger("broadcaster")
    logger.setLevel(level)

    if not logger.handlers:
        log_format = "%(asctime)s | %(levelname)-8s | [%(filename)s:%(lineno)d] | %(funcName)s() | %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        if log_file:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
            logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColoredFormatter(log_format, datefmt=date_format))

        logger.addHandler(console_handler)

    return logger
