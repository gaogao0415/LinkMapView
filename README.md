# LinkMapView

**Your linker map has the data. LinkMapView gives it a map.** Turn TI linker `.map` files into an interactive HTML memory report, without drowning in raw linker output.

[简体中文](README_CN.md)

## What It Does

- Parses `MEMORY CONFIGURATION` and `SECTION ALLOCATION MAP` from TI linker map files.
- Summarizes memory capacity, used and remaining space, and utilization. When usage fields are absent, usage is estimated from output section address ranges.
- Ranks memory regions, output sections, libraries, and object files by usage.
- Searches and filters recognized input-section details, then exports the current results to CSV.
- Skips ambiguous input records and reports the count. No pretending that an address is a size.

## Get Started

Requirements: Python 3 and Tkinter. No third-party Python packages are needed. Tkinter is included with many Python distributions; on some Linux systems, install it separately (for example, `sudo apt install python3-tk` on Debian/Ubuntu).

Install the project's Python requirements and launch the app:

```bash
python -m pip install -r requirement.txt
python LinkMapView.py
```

Choose a TI linker `.map` file in the window and click **Generate HTML**. By default, the report lands beside the map file with the same base name and an `.html` extension. Pick a different output path in the app if you prefer. Open the generated HTML in a browser; filter the details, inspect the rankings, and export the current view as CSV.

No desktop display (for example, in a container or SSH session)? Pass the map file directly; the report is generated without opening a GUI:

```bash
python LinkMapView.py path/to/firmware.map
python LinkMapView.py path/to/firmware.map --output path/to/report.html
```

Without `--output`, the HTML report is written next to the map file using the same base name.

## A Note on Parsing

LinkMapView targets TI linker map files containing `MEMORY CONFIGURATION` and `SECTION ALLOCATION MAP`. Input details include only records the parser can identify unambiguously. If no output sections appear, check that the map file contains `SECTION ALLOCATION MAP`.
