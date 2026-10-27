# Third-party notices

## Scope

The [MIT License](LICENSE) covers original hdUE code, documentation, and project-generated synthetic samples. Unreal Engine, Houdini, vendor SDKs, third-party libraries, and separately supplied assets remain subject to their own licenses.

This source repository does not bundle those engines, SDKs, libraries, or third-party assets. Users obtain them separately. The references below identify principal dependencies; they do not replace the license files supplied with the versions actually used.

## Unreal Engine acknowledgement

hdUE uses Unreal® Engine. Unreal® is a trademark or registered trademark of Epic Games, Inc. in the United States of America and elsewhere.

Unreal® Engine, Copyright 1998 – 2026, Epic Games, Inc. All rights reserved.

Unreal Engine is governed by the [Unreal Engine End User License Agreement](https://www.unrealengine.com/eula/unreal). Section 7(a) requires preservation of Epic's existing proprietary notices and specifies the attribution above for products that have credits. Keep the year current in release credits. Section 7(b) also addresses licenses and notices for third-party software supplied with the engine. The acknowledgement does not grant permission to use Epic logos or imply Epic's endorsement.

## Principal dependencies

| Component | Use in hdUE | License reference |
| --- | --- | --- |
| Unreal Engine | Embedded rendering backend and asset preparation | [Epic EULA](https://www.unrealengine.com/eula/unreal) |
| Houdini and its SDK | Optional Solaris viewport integration | [SideFX license agreement](https://www.sidefx.com/legal/license-agreement/) and terms supplied with the installation |
| OpenUSD; Houdini-supplied USD | Hydra delegate and scene processing | [OpenUSD license and component notices](https://github.com/PixarAnimationStudios/OpenUSD/blob/release/LICENSE.txt), including the Tomorrow Open Source Technology License 1.0; use the notices supplied with Houdini for its USD build |
| MaterialX | Material graph export and conversion utilities | [Apache License 2.0](https://github.com/AcademySoftwareFoundation/MaterialX/blob/main/LICENSE) and accompanying upstream notices |
| PySide6 / Qt | Optional usdview user interface | [Qt licensing](https://www.qt.io/licensing): LGPLv3/GPLv3 community or commercial terms, as applicable; also see [Qt for Python component notices](https://doc.qt.io/qtforpython/licenses.html) |
| PyOpenGL | Optional usdview OpenGL integration | [Upstream license collection](https://github.com/mcfletch/pyopengl/blob/master/license.txt), including BSD-style licenses; retain the collection supplied with the installed release |
| NumPy | usdview numerical utilities and HDR image comparison/preview scripts | [BSD 3-Clause license](https://github.com/numpy/numpy/blob/main/LICENSE.txt) and additional notices supplied with the selected binary package |
| Pillow | Optional HDR-to-PNG image preview script | [Pillow/PIL license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) and additional notices supplied with the selected binary package |
| Python | Utility scripts and host integration | [Python Software Foundation License Version 2 and incorporated-software notices](https://docs.python.org/3/license.html), as supplied with the selected runtime |

SDK-provided packages can differ from standalone dependencies. The license files and notices supplied with each installed package apply. Build tools and platform SDKs also retain their respective terms.

## Redistribution

For any release that includes third-party code, binaries, runtimes, or assets, include the complete applicable license texts, copyright notices, disclaimers, and any required source or relinking materials for those exact components, including their transitive dependencies. Links in this document are an inventory for this source repository, not a complete notice bundle for a binary release.

Distributing a backend that contains Unreal Engine code is subject to the Epic EULA in addition to hdUE's MIT license. Check Sections 4 and 5 for the applicable distribution terms and Section 6(d) if a package includes Engine Tools, including Editor or Developer modules. Houdini SDK redistribution and Qt licensing likewise depend on the components and distribution method selected. An attribution notice alone does not authorize redistribution.
