# Wacom Tracker
A program that tracks usage of a Wacom Tablet.

## AI Usage
Note: That this program was largly made by AI models (e.g. Gemini 4 Hydrogen) and therefore contributions with AI are obviously allowed.

## Launching

### Requirements:
- Windows only (for now at least)
- Python 3.10 or newer
- Tkinter (should be included on a default installation of python)
- The standard Wacom tablet drivers have to be installed and running

### 3rd Party Dependencies:
- ```pynput```
- ```pystray```
- ```pillow```

#### Can be installed with:
```
pip install pynput pystray pillow
```
#### Verify all are installed with:
```
python -c "import pynput, pystray, PIL; print('All dependencies OK')"
```
If it says "All dependencies OK" then all the dependencies are installed.

### Setup: 
1. Download the script and put it in a folder you intend to keep it in (Take note of this )
    - The program will also store its data it this folder.
2. (Optional) Create a shortcut to the script and put it in your Start menu folder (```%AppData%\Microsoft\Windows\Start Menu\Programs```)
3. Install all the dependencies with the commands in the dependency section
4. Run the program
    - On the first start the program will ask for your tablet and display settings for accurate tracking

### Running:
* Double click ```wacom_tracker.pyw```
* In a terminal window run:  ```pythonw wacom_tracker.pyw```

## Features
* Accurate tracking of distance drawing/hovering/both using a Wacom tablet based on physical dimensions and pixel mappings
* Saves data in a easily readable JSON file
* Bar graph with distance data per day in the week/month or month in the year
* Histogram with distance data for each day tracked that can be sorted by date or value
* UI for easy setup and change of tablet and display settings and mappings to maintain accurate tracking
* Minimises to the system-tray for background tracking

## Code of conduct
Please check out our [Code of  Conduct](https://github.com/Djkcool/wacom-tracker/blob/main/CODE_OF_CONDUCT.md) for details on our code of conduct.

[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](https://github.com/Djkcool/wacom-tracker/blob/main/CODE_OF_CONDUCT.md)

## Contributing
Please check out [CONTRIBUTING.md](https://github.com/Djkcool/wacom-tracker/blob/main/CONTRIBUTING.md) for details on how to contribute to this project, **before submitting a pull request or creating an issue**.

## Versioning

We use [SemVer](http://semver.org/) for versioning. For the versions available, check out the [releases on this repository](https://github.com/Djkcool/wacom-tracker/releases).

## License
This script uses the [MIT License](https://choosealicense.com/licenses/mit/) - Check out the [LICENSE](https://github.com/Djkcool/wacom-tracker/blob/main/LICENSE) file for details.

## Authors
* [**Djkcool**](https://github.com/Djkcool) - *Initial work*

Also check out the list of [contributors](https://github.com/Djkcool/wacom-tracker/contributors) who participated in this project.