# Windows .exe (you can do now on your PC)
Use the packager you already have in package_backend_gui.py:

Open terminal at project root.
### Install build deps:

'''python -m pip install --upgrade pip

pip install -r requirements.txt

pip install pyinstaller'''

### Build:
python tools/package_backend_gui.py

### Output will be in:
dist/chatapp-backend-launcher-windows-amd64/

exe file inside that folder (same launcher name).


#
# macOS .dmg (cannot be built natively on Windows)
You have 2 practical options:

## Option A (recommended): GitHub Actions (Mac runner)
Run workflow in backend-launcher-build.yml, download mac artifact, then on a Mac create dmg with:

'''hdiutil create -volname "ChatApp Backend Launcher" -srcfolder dist/chatapp-backend-launcher-darwin-arm64 -ov -format UDZO chatapp-backend-launcher.dmg'''

## Option B: build directly on a Mac

python -m pip install -r [requirements.txt](http://_vscodecontentref_/2) pyinstaller

python tools/package_backend_gui.py

hdiutil create -volname "ChatApp Backend Launcher" -srcfolder dist/chatapp-backend-launcher-darwin-arm64 -ov -format UDZO chatapp-backend-launcher.dmg