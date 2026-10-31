# Third-party notices

## GeoGebra

Parts of this project are a Python/GLSL translation of GeoGebra's interval plotting code.
The derived files are:

- `src/qmlmathplot/interval.py`
- `src/qmlmathplot/ggb_path.py`
- `src/qmlmathplot/ggb_compute.py`
- `src/qmlmathplot/ggb_reference.py`

Original work: GeoGebra, `source/shared/common/src/main/java/org/geogebra/common/euclidian/plot/interval/`
and `source/shared/common/src/main/java/org/geogebra/common/kernel/interval/`,
Copyright (c) GeoGebra GmbH, licensed under the **European Union Public Licence v. 1.2
(EUPL-1.2)**. The full licence text is in [`LICENSES/EUPL-1.2.txt`](LICENSES/EUPL-1.2.txt);
the upstream project is <https://github.com/geogebra/geogebra>.

The files above were translated/ported and modified in 2026. Under EUPL-1.2 Article 5 and its
Appendix, a derivative work combining EUPL-1.2 code with a work under a compatible licence may
be distributed under that compatible licence; this project is distributed under
**GPL-3.0-only**, and the GeoGebra copyright and licence notices are retained here.

> GeoGebra's file headers also state that the overall GeoGebra software package is free to use
> for non-commercial purposes only. This project is a personal, non-commercial work. Anyone
> intending a commercial distribution should review that statement with the upstream project.

## Qt / PySide6

PySide6 is licensed under `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; this project uses it
under GPL-3.0-only. See the licence files shipped in the PySide6 wheel.

## SymPy

SymPy is BSD-licensed. See <https://github.com/sympy/sympy/blob/master/LICENSE>.

## Matplotlib (optional)

The optional matplotlib hand-off depends on matplotlib, which is distributed under the
matplotlib licence (PSF-based). See <https://matplotlib.org/stable/users/project/license.html>.
