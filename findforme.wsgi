#!/usr/bin/python
import sys
import os

import app

# Activate your virtual environment
venv_path = "/root/qjump-api/.venv"
activate_this = os.path.join(venv_path, "bin/activate_this.py")
with open(activate_this) as file_:
    exec(file_.read(), dict(__file__=activate_this))

# Add the directory containing your app to the Python path
app_path = '/root/qjump-api'
sys.path.insert(0, app_path)

print(f'####### Starting app on {app_path}')

from app import app as application

print(f'####### Testing app: {app.home()}')
