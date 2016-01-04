# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2015-2016 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Steps for bdd-like tests."""

import re
import time
import json
import os.path
import logging
import collections

import pytest
import yaml
import pytest_bdd as bdd
from PyQt5.QtCore import QElapsedTimer
from PyQt5.QtGui import QClipboard

from helpers import utils


def _clipboard_mode(qapp, what):
    """Get the QClipboard::Mode to use based on a string."""
    if what == 'clipboard':
        return QClipboard.Clipboard
    elif what == 'primary selection':
        assert qapp.clipboard().supportsSelection()
        return QClipboard.Selection
    else:
        raise AssertionError


## Given


@bdd.given(bdd.parsers.parse("I set {sect} -> {opt} to {value}"))
def set_setting_given(quteproc, httpbin, sect, opt, value):
    """Set a qutebrowser setting.

    This is available as "Given:" step so it can be used as "Background:".
    """
    value = value.replace('(port)', str(httpbin.port))
    quteproc.set_setting(sect, opt, value)


@bdd.given(bdd.parsers.parse("I open {path}"))
def open_path_given(quteproc, path):
    """Open a URL.

    This is available as "Given:" step so it can be used as "Background:".

    It always opens a new tab, unlike "When I open ..."
    """
    quteproc.open_path(path, new_tab=True)


@bdd.given(bdd.parsers.parse("I run {command}"))
def run_command_given(quteproc, command):
    """Run a qutebrowser command.

    This is available as "Given:" step so it can be used as "Background:".
    """
    quteproc.send_cmd(command)


@bdd.given("I have a fresh instance")
def fresh_instance(quteproc):
    """Restart qutebrowser instance for tests needing a fresh state."""
    quteproc.terminate()
    quteproc.start()


## When


@bdd.when(bdd.parsers.parse("I open {path}"))
def open_path(quteproc, path):
    """Open a URL.

    If used like "When I open ... in a new tab", the URL is opened ina new
    tab.
    """
    new_tab_suffix = ' in a new tab'
    if path.endswith(new_tab_suffix):
        path = path[:-len(new_tab_suffix)]
        new_tab = True
    else:
        new_tab = False

    quteproc.open_path(path, new_tab=new_tab)


@bdd.when(bdd.parsers.parse("I set {sect} -> {opt} to {value}"))
def set_setting(quteproc, httpbin, sect, opt, value):
    """Set a qutebrowser setting."""
    value = value.replace('(port)', str(httpbin.port))
    quteproc.set_setting(sect, opt, value)


@bdd.when(bdd.parsers.parse("I run {command}"))
def run_command(quteproc, httpbin, command):
    """Run a qutebrowser command.

    The suffix "with count ..." can be used to pass a count to the command.
    """
    if 'with count' in command:
        command, count = command.split(' with count ')
        count = int(count)
    else:
        count = None
    command = command.replace('(port)', str(httpbin.port))
    quteproc.send_cmd(command, count=count)


@bdd.when(bdd.parsers.parse("I reload"))
def reload(qtbot, httpbin, quteproc, command):
    """Reload and wait until a new request is received."""
    with qtbot.waitSignal(httpbin.new_request, raising=True):
        quteproc.send_cmd(':reload')


@bdd.when(bdd.parsers.parse("I wait until {path} is loaded"))
def wait_until_loaded(quteproc, path):
    """Wait until the given path is loaded (as per qutebrowser log)."""
    quteproc.wait_for_load_finished(path)


@bdd.when(bdd.parsers.re(r'I wait for (?P<is_regex>regex )?"'
                         r'(?P<pattern>[^"]+)" in the log'))
def wait_in_log(quteproc, is_regex, pattern):
    """Wait for a given pattern in the qutebrowser log.

    If used like "When I wait for regex ... in the log" the argument is treated
    as regex. Otherwise, it's treated as a pattern (* can be used as wildcard).
    """
    if is_regex:
        pattern = re.compile(pattern)
    quteproc.wait_for(message=pattern)


@bdd.when(bdd.parsers.re(r'I wait for the (?P<category>error|message|warning) '
                         r'"(?P<message>.*)"'))
def wait_for_message(quteproc, httpbin, category, message):
    """Wait for a given statusbar message/error/warning."""
    expect_message(quteproc, httpbin, category, message)


@bdd.when(bdd.parsers.parse("I wait {delay}s"))
def wait_time(quteproc, delay):
    """Sleep for the given delay."""
    time.sleep(float(delay))


@bdd.when(bdd.parsers.re('I press the keys? "(?P<keys>[^"]*)"'))
def press_keys(quteproc, keys):
    """Send the given fake keys to qutebrowser."""
    quteproc.press_keys(keys)


@bdd.when("selection is supported")
def selection_supported(qapp):
    """Skip the test if selection isn't supported."""
    if not qapp.clipboard().supportsSelection():
        pytest.skip("OS doesn't support primary selection!")


@bdd.when(bdd.parsers.re(r'I put "(?P<content>.*)" into the '
                         r'(?P<what>primary selection|clipboard)'))
def fill_clipboard(qtbot, qapp, httpbin, what, content):
    mode = _clipboard_mode(qapp, what)
    content = content.replace('(port)', str(httpbin.port))

    clipboard = qapp.clipboard()
    with qtbot.waitSignal(clipboard.changed):
        clipboard.setText(content, mode)


## Then


@bdd.then(bdd.parsers.parse("{path} should be loaded"))
def path_should_be_loaded(httpbin, path):
    """Make sure the given path was loaded from the webserver."""
    httpbin.wait_for(verb='GET', path='/' + path)


@bdd.then(bdd.parsers.parse("The requests should be:\n{pages}"))
def list_of_requests(httpbin, pages):
    """Make sure the given requests were done from the webserver."""
    expected_requests = [httpbin.ExpectedRequest('GET', '/' + path.strip())
                         for path in pages.split('\n')]
    actual_requests = httpbin.get_requests()
    assert actual_requests == expected_requests


@bdd.then(bdd.parsers.parse("The unordered requests should be:\n{pages}"))
def list_of_requests_unordered(httpbin, pages):
    """Make sure the given requests were done (in no particular order)."""
    expected_requests = [httpbin.ExpectedRequest('GET', '/' + path.strip())
                         for path in pages.split('\n')]
    actual_requests = httpbin.get_requests()
    # Requests are not hashable, we need to convert to ExpectedRequests
    actual_requests = [httpbin.ExpectedRequest.from_request(req)
                       for req in actual_requests]
    assert (collections.Counter(actual_requests) ==
            collections.Counter(expected_requests))


@bdd.then(bdd.parsers.re(r'the (?P<category>error|message|warning) '
                         r'"(?P<message>.*)" should be shown'))
def expect_message(quteproc, httpbin, category, message):
    """Expect the given message in the qutebrowser log."""
    category_to_loglevel = {
        'message': logging.INFO,
        'error': logging.ERROR,
        'warning': logging.WARNING,
    }
    message = message.replace('(port)', str(httpbin.port))
    quteproc.mark_expected(category='message',
                           loglevel=category_to_loglevel[category],
                           message=message)


@bdd.then(bdd.parsers.re(r'(?P<is_regex>regex )?"(?P<pattern>[^"]+)" should '
                         r'be logged'))
def should_be_logged(quteproc, is_regex, pattern):
    """Expect the given pattern on regex in the log."""
    if is_regex:
        pattern = re.compile(pattern)
    quteproc.wait_for(message=pattern)


@bdd.then(bdd.parsers.parse('"{pattern}" should not be logged'))
def ensure_not_logged(quteproc, pattern):
    """Make sure the given pattern was *not* logged."""
    quteproc.ensure_not_logged(message=pattern)


@bdd.then(bdd.parsers.parse('the javascript message "{message}" should be '
                            'logged'))
def javascript_message_logged(quteproc, message):
    """Make sure the given message was logged via javascript."""
    quteproc.wait_for(category='js', function='javaScriptConsoleMessage',
                      message='[*] {}'.format(message))


@bdd.then(bdd.parsers.parse('the javascript message "{message}" should not be '
                            'logged'))
def javascript_message_not_logged(quteproc, message):
    """Make sure the given message was *not* logged via javascript."""
    quteproc.ensure_not_logged(category='js',
                               function='javaScriptConsoleMessage',
                               message='[*] {}'.format(message))


@bdd.then(bdd.parsers.parse("The session should look like:\n{expected}"))
def compare_session(quteproc, expected):
    """Compare the current sessions against the given template.

    partial_compare is used, which means only the keys/values listed will be
    compared.
    """
    # Translate ... to ellipsis in YAML.
    loader = yaml.SafeLoader(expected)
    loader.add_constructor('!ellipsis', lambda loader, node: ...)
    loader.add_implicit_resolver('!ellipsis', re.compile(r'\.\.\.'), None)

    data = quteproc.get_session()
    expected = loader.get_data()
    assert utils.partial_compare(data, expected)


@bdd.then("no crash should happen")
def no_crash():
    """Don't do anything.

    This is actually a NOP as a crash is already checked in the log."""
    pass


@bdd.then(bdd.parsers.parse("the header {header} should be set to {value}"))
def check_header(quteproc, header, value):
    """Check if a given header is set correctly.

    This assumes we're on the httpbin header page.
    """
    content = quteproc.get_content()
    data = json.loads(content)
    print(data)
    assert data['headers'][header] == value


@bdd.then(bdd.parsers.parse("the page source should look like {filename}"))
def check_contents(quteproc, filename):
    """Check the current page's content.

    The filename is interpreted relative to tests/integration/data.
    """
    content = quteproc.get_content(plain=False)
    path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..',
                        'data', os.path.join(*filename.split('/')))
    with open(path, 'r', encoding='utf-8') as f:
        assert content == f.read()


@bdd.then(bdd.parsers.parse("the following tabs should be open:\n{tabs}"))
def check_open_tabs(quteproc, tabs):
    """Check the list of open tabs in the session.

    This is a lightweight alternative for "The session should look like: ...".

    It expects a list of URLs, with an optional "(active)" suffix.
    """
    session = quteproc.get_session()
    active_suffix = ' (active)'
    tabs = tabs.splitlines()
    assert len(session['windows']) == 1
    assert len(session['windows'][0]['tabs']) == len(tabs)

    for i, line in enumerate(tabs):
        line = line.strip()
        line = line[2:]  # remove "- " prefix
        if line.endswith(active_suffix):
            path = line[:-len(active_suffix)]
            active = True
        else:
            path = line
            active = False

        session_tab = session['windows'][0]['tabs'][i]
        assert session_tab['history'][-1]['url'] == quteproc.path_to_url(path)
        if active:
            assert session_tab['active']
        else:
            assert 'active' not in session_tab


def _wait_for_clipboard(qtbot, clipboard, mode, expected):
    timeout = 1000
    timer = QElapsedTimer()
    timer.start()

    while True:
        if clipboard.text(mode=mode) == expected:
            return
        with qtbot.waitSignal(clipboard.changed, timeout=timeout) as blocker:
            pass
        if not blocker.signal_triggered or timer.hasExpired(timeout):
            mode_names = {
                QClipboard.Clipboard: 'clipboard',
                QClipboard.Selection: 'primary selection',
            }
            raise WaitForTimeout(
                "Timed out after {}ms waiting for {} in {}.".format(
                    timeout, expected, mode_names[mode]))


@bdd.then(bdd.parsers.re(r'the (?P<what>primary selection|clipboard) should '
                         r'contain "(?P<content>.*)"'))
def clipboard_contains(qtbot, qapp, httpbin, what, content):
    mode = _clipboard_mode(qapp, what)
    expected = content.replace('(port)', str(httpbin.port))
    expected = expected.replace('\\n', '\n')
    _wait_for_clipboard(qtbot, qapp.clipboard(), mode, expected)


@bdd.then(bdd.parsers.parse('the clipboard should contain:\n{content}'))
def clipboard_contains_multiline(qtbot, qapp, content):
    expected = '\n'.join(line.strip() for line in content.splitlines())
    _wait_for_clipboard(qtbot, qapp.clipboard(), QClipboard.Clipboard,
                        expected)
