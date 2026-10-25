import io
import unittest
from unittest.mock import patch

from modules.console_io import configure_utf8_output


class ConsoleEncodingTests(unittest.TestCase):
    def test_chinese_stdout_and_stderr_are_utf8_even_when_initially_gbk(self):
        output, errors = io.BytesIO(), io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding='gbk', newline='\n')
        stderr = io.TextIOWrapper(errors, encoding='gbk', newline='\n')
        with patch('sys.stdout', stdout), patch('sys.stderr', stderr):
            configure_utf8_output()
            stdout.write('课程日志：正在学习 ✓\n')
            stderr.write('错误提示：连接超时\n')
            stdout.flush()
            stderr.flush()
        self.assertEqual(output.getvalue().decode('utf-8'), '课程日志：正在学习 ✓\n')
        self.assertEqual(errors.getvalue().decode('utf-8'), '错误提示：连接超时\n')

    def test_capture_streams_and_missing_console_are_supported(self):
        captured = io.StringIO()
        with patch('sys.stdout', captured), patch('sys.stderr', None):
            configure_utf8_output()
        self.assertEqual(captured.getvalue(), '')
