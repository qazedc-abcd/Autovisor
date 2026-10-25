# Third-party notices

Autovisor is derived from [CXRunfree/Autovisor](https://github.com/CXRunfree/Autovisor).
The original MIT copyright notice is retained in LICENSE.

The desktop interface uses Qt for Python (PySide6 Essentials) and Shiboken, licensed
under LGPL-3.0/GPL-3.0 or a commercial Qt license. This distribution uses their
LGPL-3.0 option and dynamically linked Qt libraries. Their license texts are
included in the licenses directory of the Windows package. The libraries can be
replaced by compatible builds; this application does not prohibit reverse
engineering for debugging modifications to those libraries.

- Qt for Python source and license information: https://code.qt.io/pyside/pyside-setup.git/
- Qt source and license information: https://code.qt.io/qt/
- LGPL-3.0: https://www.gnu.org/licenses/lgpl-3.0.html

Other runtime dependencies retain their upstream licenses, including Playwright,
NumPy, OpenCV, HTTPX, Requests, Pillow and PyInstaller. Python and their shared
libraries are bundled to allow the Windows application to run without installing
Python separately. Browser software is not bundled.
