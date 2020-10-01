#
# Copyright (c) 2020, Carlos Rodrigues
#


all: pialarm-venv


pialarm-venv:
	python3 -m venv pialarm-venv
	pialarm-venv/bin/pip install --upgrade pip setuptools wheel
	pialarm-venv/bin/pip install -r requirements.txt

	@printf "\nCreated a new Python 3.x virtual environment with the necessary dependencies.\n"
	@printf "You can activate it using the following command: '. pialarm-venv/bin/activate'\n"


clean:
	find . -name '*~' -type f -delete
	find . -name '*.pyc' -type f -delete
	find . -name '__pycache__' -type d -delete
	find . -name '*.egg-info' -type d -delete


.PHONY: all clean
