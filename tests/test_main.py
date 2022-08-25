from copy import deepcopy
import subprocess
from unittest.mock import patch, call, Mock
from sys import executable as python

from pytest import raises

from pip_review.__main__ import main


class FakePopen:

    def __init__(self, stdout=None, stderr=None, returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return

    def communicate(self):
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class CopyingMock(Mock):
    # Popen args are mutated inside update_packages package and are not
    # captured correctly. This class is workaround. See:
    # https://docs.python.org/3/library/unittest.mock-examples.html#coping-with-mutable-arguments

    def __call__(self, *args, **kwargs):
        return super().__call__(*deepcopy(args), **kwargs)


def outdated_call(forwarded=None):
    args = [
        python, '-m', 'pip', 'list', '--outdated', '--disable-pip-version-check', '--format=json'
    ]
    if forwarded:
        args = args[:5] + forwarded + args[5:]
    return call(args, stdout=subprocess.PIPE)


def simulate(sys_argv, fake_popens):

    def wrap(test_func):

        def run_simulated():
            logger = Mock()
            with (
                patch('sys.argv', sys_argv),
                patch('pip_review.__main__.setup_logging', return_value=logger),
                patch('subprocess.Popen', CopyingMock(side_effect=fake_popens)) as popen
            ):
                main()
                test_func(popen, logger)

        return run_simulated

    return wrap


UP_TO_DATE = FakePopen(b'[]\r\n')


@simulate(
    [''],
    [UP_TO_DATE],
)
def test_everything_is_up_to_date(popen, logger):
    assert popen.call_args_list == [outdated_call()]
    assert logger.mock_calls == [call.info('Everything up-to-date')]


@simulate(
    [''],
    [
        FakePopen(
            b'[{"name": "setuptools", "version": "65.1.1", "latest_version": "65.3.0", "latest_filetype": "wheel"}]\r\n'
        ),
    ],
)
def test_single_outdated_package(popen, logger):
    assert popen.call_args_list == [outdated_call()]
    assert logger.mock_calls == [call.info('setuptools==65.3.0 is available (you have 65.1.1)')]


OUTDATED_SETUPTOOLS = FakePopen(
    b'[{"name": "setuptools", "version": "65.1.1", "latest_version": "65.3.0", "latest_filetype": "wheel"}]\r\n'
)


@simulate(
    ['', '--raw'],
    [OUTDATED_SETUPTOOLS],
)
def test_raw_option(popen, logger):
    assert popen.call_args_list == [outdated_call()]
    assert logger.mock_calls == [call.info('setuptools==65.3.0')]


@simulate(
    ['', '--timeout', '30'],
    [UP_TO_DATE],
)
def test_forwarding_unrecognized_args(popen, logger):
    assert popen.call_args == outdated_call(['--timeout', '30'])
    assert logger.mock_calls == [call.info('Everything up-to-date')]


def test_forwarding_unrecognized_args_fails():
    with raises(subprocess.CalledProcessError):
        simulate(
            ['', '--bananas'],
            [FakePopen(b'[]\r\n', returncode=-1)],
        )(lambda: None)()


@simulate(
    ['', '--auto'],
    [UP_TO_DATE],
)
def test_auto_up_to_date(popen, logger):
    assert popen.call_args_list == [outdated_call()]
    assert logger.mock_calls == [call.info('Everything up-to-date')]


@simulate(
    ['', '--auto', '--force-reinstall'],
    [OUTDATED_SETUPTOOLS, FakePopen()],
)
def test_forwarding_to_install_not_list(popen, logger):
    assert popen.call_args_list[0] == outdated_call()
    assert popen.call_args_list[1].args[0] == [
        python, '-m', 'pip', 'install', '-U', '--force-reinstall', 'setuptools']
    assert popen.call_count == 2
    assert not logger.mock_calls


@simulate(
    ['', '--auto', '--not-required'],
    [OUTDATED_SETUPTOOLS, FakePopen()],
)
def test_forwarding_to_list_not_install(popen, logger):
    assert popen.call_args_list[0] == outdated_call(['--not-required'])
    assert popen.call_args.args[0] == [
        python, '-m', 'pip', 'install', '-U', 'setuptools']
    assert popen.call_count == 2
    assert not logger.mock_calls


@simulate(
    ['', '--auto', '--continue-on-fail'],
    [
        FakePopen(
            b'[{"name": "badpackage", "version": "0.1", "latest_version": "0.2", "latest_filetype": "wheel"},'
            b' {"name": "setuptools", "version": "65.1.1", "latest_version": "65.3.0", "latest_filetype": "wheel"}]\n'
        ),
        FakePopen(returncode=-1),  # fail on badpackage
        FakePopen()  # continue with installing setuptools
    ],
)
def test_two_packages_one_failing_continue(popen, logger):
    assert popen.call_args_list[0] == outdated_call()
    assert popen.call_args_list[1].args[0] == [
        python, '-m', 'pip', 'install', '-U', 'badpackage']
    assert popen.call_args_list[2].args[0] == [
        python, '-m', 'pip', 'install', '-U', 'setuptools']
    assert popen.call_count == 3
    assert not logger.mock_calls
