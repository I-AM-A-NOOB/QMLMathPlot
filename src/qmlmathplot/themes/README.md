# Vendored Matplotlib style sheets

Origin: [matplotlib](https://github.com/matplotlib/matplotlib), path
`lib/matplotlib/mpl-data/stylelib/<name>.mplstyle`, from branch `main` at commit
`4dd8b5c29a0cf54a4fcef5012ee3c54a26d252ce` (fetched 2026-10-08). The files are copied
verbatim; only line endings were normalised to LF.

They are Matplotlib's own style sheets, redistributed here under Matplotlib's BSD
license — see `LICENSE.matplotlib` in this directory.

Our loader converts the `rcParams` keys found in these files into plot style
properties and silently ignores keys it does not recognise.

Dropping a new `*.mplstyle` file into this directory makes it selectable by its file
name (the name without the extension), with no registration step.
