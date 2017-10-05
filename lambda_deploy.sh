#!/bin/sh

# Specify the python and pip command paths
PIP=pip3

if [ -z $ENV ]; then
    export ENV=dev
fi

# Install VirtualEnv for the project

declare -a cmdArgs='([0]=$PIP [1]="install virtualenv")'
${cmdArgs[@]}
virtualenv expenditures
source expenditures/bin/activate

# Install packages
pip install -r expenditures/requirements.txt

# Start Zappa Update
python appendFirebaseCert.py
cd expenditures

if zappa deploy $ENV; then
    echo "Zappa Deploy Done"
else
    zappa update $ENV
    echo "Zappa Update Done"
fi

# Cleanup
deactivate
rm -rf lib/ include/ bin/ pip-selfcheck.json
rm zappa_settings.json

cd ..
