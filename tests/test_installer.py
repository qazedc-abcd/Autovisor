import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from modules.installer import (
    DEFAULT_PACKAGES,
    build_wheel_url,
    install_package,
    is_installed,
    is_compatible_wheel,
    runtime_packages,
    validate_python_version,
    wheel_tags,
)
from modules.progress import show_progress


class InstallerTests(unittest.TestCase):
    def test_rejects_unsupported_python_version(self):
        for minor in (9, 14):
            with self.assertRaises(RuntimeError):
                validate_python_version(SimpleNamespace(major=3, minor=minor))

    def test_accepts_supported_python_versions(self):
        for minor in (10, 11, 12, 13):
            validate_python_version(SimpleNamespace(major=3, minor=minor))

    def test_runtime_packages_match_python_version(self):
        self.assertEqual(
            {"numpy": "2.1.3", "opencv-python": "4.10.0.84"},
            runtime_packages(SimpleNamespace(major=3, minor=13)),
        )
        for minor in (10, 11, 12):
            self.assertEqual(
                DEFAULT_PACKAGES,
                runtime_packages(SimpleNamespace(major=3, minor=minor)),
            )

    def test_parses_wheel_tags(self):
        self.assertEqual(
            wheel_tags("numpy-1.26.4-cp311-cp311-win_amd64.whl"),
            ("1.26.4", "cp311", "cp311", "win_amd64"),
        )

    def test_requires_matching_python_abi_and_platform(self):
        self.assertTrue(is_compatible_wheel(
            "numpy-1.26.4-cp311-cp311-win_amd64.whl",
            "numpy", "1.26.4", "cp311", "cp311", "win_amd64",
        ))
        self.assertFalse(is_compatible_wheel(
            "numpy-1.26.4-cp310-cp310-win_amd64.whl",
            "numpy", "1.26.4", "cp311", "cp311", "win_amd64",
        ))

    def test_accepts_opencv_abi3_wheel(self):
        self.assertTrue(is_compatible_wheel(
            "opencv_python-4.10.0.82-cp37-abi3-win_amd64.whl",
            "opencv-python", "4.10.0.82", "cp311", "cp311", "win_amd64",
        ))

    def test_opencv_wheel_build_must_match_exactly(self):
        self.assertFalse(is_compatible_wheel(
            "opencv_python-4.10.0.82-cp37-abi3-win_amd64.whl",
            "opencv-python", "4.10.0.84", "cp313", "cp313", "win_amd64",
        ))
        self.assertTrue(is_compatible_wheel(
            "opencv_python-4.10.0.84-cp37-abi3-win_amd64.whl",
            "opencv-python", "4.10.0.84", "cp313", "cp313", "win_amd64",
        ))

    def test_wrong_opencv_build_is_not_imported_before_replacement(self):
        with patch('modules.installer.distribution_version', return_value='4.10.0.82'), \
                patch('modules.installer.import_module') as import_module:
            self.assertEqual(is_installed('opencv-python', '4.10.0.84'), (None, False))
        import_module.assert_not_called()

    def test_cv2_module_version_does_not_include_wheel_build(self):
        with patch('modules.installer.distribution_version', return_value='4.10.0.84'), \
                patch('modules.installer.import_module', return_value=SimpleNamespace(__version__='4.10.0')):
            self.assertTrue(is_installed('opencv-python', '4.10.0.84')[1])

    def test_locked_native_library_stops_without_trying_other_mirrors(self):
        with patch('modules.installer.download_wheel', return_value='opencv.whl') as download, \
                patch('modules.installer.clear_package_files', side_effect=PermissionError('cv2.pyd locked')):
            with self.assertRaisesRegex(RuntimeError, '关闭旧的 Autovisor'):
                install_package('opencv-python', '4.10.0.84', [('one', 'https://one'), ('two', 'https://two')])
        self.assertEqual(download.call_count, 1)

    def test_native_import_failure_does_not_retry_install_in_same_process(self):
        with patch('modules.installer.download_wheel', return_value='opencv.whl') as download, \
                patch('modules.installer.clear_package_files'), patch('modules.installer.extract_whl'), \
                patch('modules.installer.import_module', side_effect=ImportError('numpy.core.multiarray failed')):
            with self.assertRaisesRegex(RuntimeError, '安装后导入失败'):
                install_package('opencv-python', '4.10.0.84', [('one', 'https://one'), ('two', 'https://two')])
        self.assertEqual(download.call_count, 1)

    def test_rejects_free_threaded_wheel(self):
        self.assertTrue(is_compatible_wheel(
            "numpy-2.1.3-cp313-cp313-win_amd64.whl",
            "numpy", "2.1.3", "cp313", "cp313", "win_amd64",
        ))
        self.assertFalse(is_compatible_wheel(
            "numpy-2.1.3-cp313-cp313t-win_amd64.whl",
            "numpy", "2.1.3", "cp313", "cp313", "win_amd64",
        ))

    def test_progress_handles_missing_content_length(self):
        output = StringIO()
        with redirect_stdout(output):
            show_progress("下载进度:", current=512, total=0)
        self.assertIn("已下载 512 bytes", output.getvalue())

    def test_resolves_relative_mirror_wheel_url(self):
        url = build_wheel_url(
            "https://mirrors.example/repository/pypi/simple/numpy/",
            "../../packages/abc/numpy-1.26.4-cp310-cp310-win_amd64.whl#sha256=test",
        )
        self.assertEqual(
            url,
            "https://mirrors.example/repository/pypi/packages/abc/numpy-1.26.4-cp310-cp310-win_amd64.whl",
        )

    @patch("modules.installer.import_module")
    @patch("modules.installer.extract_whl")
    @patch("modules.installer.clear_package_files")
    @patch("modules.installer.download_wheel")
    def test_falls_back_when_a_mirror_fails(
        self, download_wheel, clear_package_files, extract_whl, import_module
    ):
        download_wheel.side_effect = [
            RuntimeError("429 Client Error"),
            "numpy-1.26.4.whl",
        ]
        import_module.return_value = object()

        with patch("modules.installer.get_res_dir", return_value="packages"):
            result = install_package(
                "numpy", "1.26.4", [("华为", "https://huawei"), ("清华", "https://tsinghua")]
            )

        self.assertIsNotNone(result)
        self.assertEqual(download_wheel.call_count, 2)
        self.assertEqual(download_wheel.call_args_list[1].args[:3],
                         ("清华", "https://tsinghua", "numpy"))


if __name__ == "__main__":
    unittest.main()
