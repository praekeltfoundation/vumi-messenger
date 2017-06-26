help:
	@echo  "usage: make <target>"
	@echo  "Targets:"
	@echo  "    up          Updates dependencies"
	@echo  "    deps        Ensure dev dependencies are installed"
	@echo  "    check	Checks that build is sane"
	@echo  "    test	Runs all tests"
	@echo  "    run 	Runs the devserver"

up:
	@cp etc/requirements.in requirements.txt
	@pip-compile -o requirements_dev.txt etc/requirements_dev.in

deps:
	@pip install -q -r requirements_dev.txt

check: deps
	flake8 vxmessenger
	python setup.py check -mrs

test: deps
	py.test --ds=vxmessenger.webapp.settings --cov=vxmessenger --cov-report=term

run: deps
	jb -c etc/jb_config_dev.yaml

