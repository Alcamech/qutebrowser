# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2016 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
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

"""Base class for a wrapper over QWebView/QWebEngineView."""

import itertools

from PyQt5.QtCore import pyqtSignal, pyqtSlot, QUrl, QObject, QSizeF
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QWidget, QApplication

from qutebrowser.keyinput import modeman
from qutebrowser.config import config
from qutebrowser.utils import (utils, objreg, usertypes, message, log, qtutils,
                               urlutils)
from qutebrowser.misc import miscwidgets
from qutebrowser.browser import mouse, hints


tab_id_gen = itertools.count(0)


def create(win_id, parent=None):
    """Get a QtWebKit/QtWebEngine tab object.

    Args:
        win_id: The window ID where the tab will be shown.
        parent: The Qt parent to set.
    """
    # Importing modules here so we don't depend on QtWebEngine without the
    # argument and to avoid circular imports.
    mode_manager = modeman.instance(win_id)
    if objreg.get('args').backend == 'webengine':
        from qutebrowser.browser.webengine import webenginetab
        tab_class = webenginetab.WebEngineTab
    else:
        from qutebrowser.browser.webkit import webkittab
        tab_class = webkittab.WebKitTab
    return tab_class(win_id=win_id, mode_manager=mode_manager, parent=parent)


class WebTabError(Exception):

    """Base class for various errors."""


class TabData(QObject):

    """A simple namespace with a fixed set of attributes.

    Attributes:
        keep_icon: Whether the (e.g. cloned) icon should not be cleared on page
                   load.
        inspector: The QWebInspector used for this webview.
        viewing_source: Set if we're currently showing a source view.
        open_target: How the next clicked link should be opened.
        hint_target: Override for open_target for hints.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keep_icon = False
        self.viewing_source = False
        self.inspector = None
        self.open_target = usertypes.ClickTarget.normal
        self.hint_target = None

    def combined_target(self):
        if self.hint_target is not None:
            return self.hint_target
        else:
            return self.open_target

    @pyqtSlot(usertypes.ClickTarget)
    def _on_start_hinting(self, hint_target):
        """Emitted before a hinting-click takes place.

        Args:
            hint_target: A ClickTarget member to set self.hint_target to.
        """
        log.webview.debug("Setting force target to {}".format(hint_target))
        self.hint_target = hint_target

    @pyqtSlot()
    def _on_stop_hinting(self):
        log.webview.debug("Finishing hinting.")
        self.hint_target = None


class AbstractPrinting:

    """Attribute of AbstractTab for printing the page."""

    def __init__(self):
        self._widget = None

    def check_pdf_support(self):
        raise NotImplementedError

    def check_printer_support(self):
        raise NotImplementedError

    def to_pdf(self, filename):
        raise NotImplementedError

    def to_printer(self, printer):
        raise NotImplementedError


class AbstractSearch(QObject):

    """Attribute of AbstractTab for doing searches.

    Attributes:
        text: The last thing this view was searched for.
        _flags: The flags of the last search (needs to be set by subclasses).
        _widget: The underlying WebView widget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widget = None
        self.text = None

    def search(self, text, *, ignore_case=False, reverse=False,
               result_cb=None):
        """Find the given text on the page.

        Args:
            text: The text to search for.
            ignore_case: Search case-insensitively. (True/False/'smart')
            reverse: Reverse search direction.
            result_cb: Called with a bool indicating whether a match was found.
        """
        raise NotImplementedError

    def clear(self):
        """Clear the current search."""
        raise NotImplementedError

    def prev_result(self, *, result_cb=None):
        """Go to the previous result of the current search.

        Args:
            result_cb: Called with a bool indicating whether a match was found.
        """
        raise NotImplementedError

    def next_result(self, *, result_cb=None):
        """Go to the next result of the current search.

        Args:
            result_cb: Called with a bool indicating whether a match was found.
        """
        raise NotImplementedError


class AbstractZoom(QObject):

    """Attribute of AbstractTab for controlling zoom.

    Attributes:
        _neighborlist: A NeighborList with the zoom levels.
        _default_zoom_changed: Whether the zoom was changed from the default.
    """

    def __init__(self, win_id, parent=None):
        super().__init__(parent)
        self._widget = None
        self._win_id = win_id
        self._default_zoom_changed = False
        self._init_neighborlist()
        objreg.get('config').changed.connect(self._on_config_changed)

        # # FIXME:qtwebengine is this needed?
        # # For some reason, this signal doesn't get disconnected automatically
        # # when the WebView is destroyed on older PyQt versions.
        # # See https://github.com/The-Compiler/qutebrowser/issues/390
        # self.destroyed.connect(functools.partial(
        #     cfg.changed.disconnect, self.init_neighborlist))

    @pyqtSlot(str, str)
    def _on_config_changed(self, section, option):
        if section == 'ui' and option in ['zoom-levels', 'default-zoom']:
            if not self._default_zoom_changed:
                factor = float(config.get('ui', 'default-zoom')) / 100
                self._set_factor_internal(factor)
            self._default_zoom_changed = False
            self._init_neighborlist()

    def _init_neighborlist(self):
        """Initialize self._neighborlist."""
        levels = config.get('ui', 'zoom-levels')
        self._neighborlist = usertypes.NeighborList(
            levels, mode=usertypes.NeighborList.Modes.edge)
        self._neighborlist.fuzzyval = config.get('ui', 'default-zoom')

    def offset(self, offset):
        """Increase/Decrease the zoom level by the given offset.

        Args:
            offset: The offset in the zoom level list.

        Return:
            The new zoom percentage.
        """
        level = self._neighborlist.getitem(offset)
        self.set_factor(float(level) / 100, fuzzyval=False)
        return level

    def set_factor(self, factor, *, fuzzyval=True):
        """Zoom to a given zoom factor.

        Args:
            factor: The zoom factor as float.
            fuzzyval: Whether to set the NeighborLists fuzzyval.
        """
        if fuzzyval:
            self._neighborlist.fuzzyval = int(factor * 100)
        if factor < 0:
            raise ValueError("Can't zoom to factor {}!".format(factor))
        self._default_zoom_changed = True
        self._set_factor_internal(factor)

    def factor(self):
        raise NotImplementedError

    def set_default(self):
        default_zoom = config.get('ui', 'default-zoom')
        self._set_factor_internal(float(default_zoom) / 100)


class AbstractCaret(QObject):

    """Attribute of AbstractTab for caret browsing."""

    def __init__(self, win_id, tab, mode_manager, parent=None):
        super().__init__(parent)
        self._tab = tab
        self._win_id = win_id
        self._widget = None
        self.selection_enabled = False
        mode_manager.entered.connect(self._on_mode_entered)
        mode_manager.left.connect(self._on_mode_left)

    def _on_mode_entered(self, mode):
        raise NotImplementedError

    def _on_mode_left(self):
        raise NotImplementedError

    def move_to_next_line(self, count=1):
        raise NotImplementedError

    def move_to_prev_line(self, count=1):
        raise NotImplementedError

    def move_to_next_char(self, count=1):
        raise NotImplementedError

    def move_to_prev_char(self, count=1):
        raise NotImplementedError

    def move_to_end_of_word(self, count=1):
        raise NotImplementedError

    def move_to_next_word(self, count=1):
        raise NotImplementedError

    def move_to_prev_word(self, count=1):
        raise NotImplementedError

    def move_to_start_of_line(self):
        raise NotImplementedError

    def move_to_end_of_line(self):
        raise NotImplementedError

    def move_to_start_of_next_block(self, count=1):
        raise NotImplementedError

    def move_to_start_of_prev_block(self, count=1):
        raise NotImplementedError

    def move_to_end_of_next_block(self, count=1):
        raise NotImplementedError

    def move_to_end_of_prev_block(self, count=1):
        raise NotImplementedError

    def move_to_start_of_document(self):
        raise NotImplementedError

    def move_to_end_of_document(self):
        raise NotImplementedError

    def toggle_selection(self):
        raise NotImplementedError

    def drop_selection(self):
        raise NotImplementedError

    def has_selection(self):
        raise NotImplementedError

    def selection(self, html=False):
        raise NotImplementedError

    def follow_selected(self, *, tab=False):
        raise NotImplementedError


class AbstractScroller(QObject):

    """Attribute of AbstractTab to manage scroll position."""

    perc_changed = pyqtSignal(int, int)

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self._tab = tab
        self._widget = None

    def _init_widget(self, widget):
        self._widget = widget

    def pos_px(self):
        raise NotImplementedError

    def pos_perc(self):
        raise NotImplementedError

    def to_perc(self, x=None, y=None):
        raise NotImplementedError

    def to_point(self, point):
        raise NotImplementedError

    def delta(self, x=0, y=0):
        raise NotImplementedError

    def delta_page(self, x=0, y=0):
        raise NotImplementedError

    def up(self, count=1):
        raise NotImplementedError

    def down(self, count=1):
        raise NotImplementedError

    def left(self, count=1):
        raise NotImplementedError

    def right(self, count=1):
        raise NotImplementedError

    def top(self):
        raise NotImplementedError

    def bottom(self):
        raise NotImplementedError

    def page_up(self, count=1):
        raise NotImplementedError

    def page_down(self, count=1):
        raise NotImplementedError

    def at_top(self):
        raise NotImplementedError

    def at_bottom(self):
        raise NotImplementedError


class AbstractHistory:

    """The history attribute of a AbstractTab."""

    def __init__(self, tab):
        self._tab = tab
        self._history = None

    def __len__(self):
        return len(self._history)

    def __iter__(self):
        return iter(self._history.items())

    def current_idx(self):
        raise NotImplementedError

    def back(self):
        raise NotImplementedError

    def forward(self):
        raise NotImplementedError

    def can_go_back(self):
        raise NotImplementedError

    def can_go_forward(self):
        raise NotImplementedError

    def serialize(self):
        """Serialize into an opaque format understood by self.deserialize."""
        raise NotImplementedError

    def deserialize(self, data):
        """Serialize from a format produced by self.serialize."""
        raise NotImplementedError

    def load_items(self, items):
        """Deserialize from a list of WebHistoryItems."""
        raise NotImplementedError


class AbstractTab(QWidget):

    """A wrapper over the given widget to hide its API and expose another one.

    We use this to unify QWebView and QWebEngineView.

    Attributes:
        history: The AbstractHistory for the current tab.
        registry: The ObjectRegistry associated with this tab.

        _load_status: loading status of this page
                      Accessible via load_status() method.
        _has_ssl_errors: Whether SSL errors happened.
                         Needs to be set by subclasses.

        for properties, see WebView/WebEngineView docs.

    Signals:
        See related Qt signals.

        new_tab_requested: Emitted when a new tab should be opened with the
                           given URL.
        load_status_changed: The loading status changed
    """

    window_close_requested = pyqtSignal()
    link_hovered = pyqtSignal(str)
    load_started = pyqtSignal()
    load_progress = pyqtSignal(int)
    load_finished = pyqtSignal(bool)
    icon_changed = pyqtSignal(QIcon)
    title_changed = pyqtSignal(str)
    load_status_changed = pyqtSignal(str)
    new_tab_requested = pyqtSignal(QUrl)
    url_changed = pyqtSignal(QUrl)
    shutting_down = pyqtSignal()
    contents_size_changed = pyqtSignal(QSizeF)
    add_history_item = pyqtSignal(QUrl, QUrl, str)  # url, requested url, title

    def __init__(self, win_id, parent=None):
        self.win_id = win_id
        self.tab_id = next(tab_id_gen)
        super().__init__(parent)

        self.registry = objreg.ObjectRegistry()
        tab_registry = objreg.get('tab-registry', scope='window',
                                  window=win_id)
        tab_registry[self.tab_id] = self
        objreg.register('tab', self, registry=self.registry)

        # self.history = AbstractHistory(self)
        # self.scroller = AbstractScroller(self, parent=self)
        # self.caret = AbstractCaret(win_id=win_id, tab=self, mode_manager=...,
        #                            parent=self)
        # self.zoom = AbstractZoom(win_id=win_id)
        # self.search = AbstractSearch(parent=self)
        # self.printing = AbstractPrinting()

        self.data = TabData(parent=self)
        self._layout = miscwidgets.WrapperLayout(self)
        self._widget = None
        self._progress = 0
        self._has_ssl_errors = False
        self._load_status = usertypes.LoadStatus.none
        self._mouse_event_filter = mouse.MouseEventFilter(self, parent=self)
        self.backend = None

        # FIXME:qtwebengine  Should this be public api via self.hints?
        #                    Also, should we get it out of objreg?
        hintmanager = hints.HintManager(win_id, self.tab_id, parent=self)
        hintmanager.mouse_event.connect(self._on_hint_mouse_event)
        # pylint: disable=protected-access
        hintmanager.start_hinting.connect(self.data._on_start_hinting)
        hintmanager.stop_hinting.connect(self.data._on_stop_hinting)
        # pylint: enable=protected-access
        objreg.register('hintmanager', hintmanager, scope='tab',
                        window=self.win_id, tab=self.tab_id)

    def _set_widget(self, widget):
        # pylint: disable=protected-access
        self._widget = widget
        self._layout.wrap(self, widget)
        self.history._history = widget.history()
        self.scroller._init_widget(widget)
        self.caret._widget = widget
        self.zoom._widget = widget
        self.search._widget = widget
        self.printing._widget = widget
        self._install_event_filter()

    def _install_event_filter(self):
        raise NotImplementedError

    def _set_load_status(self, val):
        """Setter for load_status."""
        if not isinstance(val, usertypes.LoadStatus):
            raise TypeError("Type {} is no LoadStatus member!".format(val))
        log.webview.debug("load status for {}: {}".format(repr(self), val))
        self._load_status = val
        self.load_status_changed.emit(val.name)

    @pyqtSlot('QMouseEvent')
    def _on_hint_mouse_event(self, evt):
        """Post a new mouse event from a hintmanager."""
        # FIXME:qtwebengine Will this implementation work for QtWebEngine?
        #                   We probably need to send the event to the
        #                   focusProxy()?
        log.modes.debug("Hint triggered, focusing {!r}".format(self))
        self._widget.setFocus()
        QApplication.postEvent(self._widget, evt)

    @pyqtSlot(QUrl)
    def _on_link_clicked(self, url):
        log.webview.debug("link clicked: url {}, hint target {}, "
                          "open_target {}".format(
                              url.toDisplayString(),
                              self.data.hint_target, self.data.open_target))

        if not url.isValid():
            msg = urlutils.get_errstring(url, "Invalid link clicked")
            message.error(self.win_id, msg)
            self.data.open_target = usertypes.ClickTarget.normal
            return False

        target = self.data.combined_target()

        if target == usertypes.ClickTarget.normal:
            return
        elif target == usertypes.ClickTarget.tab:
            win_id = self.win_id
            bg_tab = False
        elif target == usertypes.ClickTarget.tab_bg:
            win_id = self.win_id
            bg_tab = True
        elif target == usertypes.ClickTarget.window:
            from qutebrowser.mainwindow import mainwindow
            window = mainwindow.MainWindow()
            window.show()
            win_id = window.win_id
            bg_tab = False
        else:
            raise ValueError("Invalid ClickTarget {}".format(target))

        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=win_id)
        tabbed_browser.tabopen(url, background=bg_tab)
        self.data.open_target = usertypes.ClickTarget.normal

    @pyqtSlot(QUrl)
    def _on_url_changed(self, url):
        """Update title when URL has changed and no title is available."""
        if url.isValid() and not self.title():
            self.title_changed.emit(url.toDisplayString())
        self.url_changed.emit(url)

    @pyqtSlot()
    def _on_load_started(self):
        self._progress = 0
        self._has_ssl_errors = False
        self.data.viewing_source = False
        self._set_load_status(usertypes.LoadStatus.loading)
        self.load_started.emit()

    @pyqtSlot(bool)
    def _on_load_finished(self, ok):
        if ok and not self._has_ssl_errors:
            if self.url().scheme() == 'https':
                self._set_load_status(usertypes.LoadStatus.success_https)
            else:
                self._set_load_status(usertypes.LoadStatus.success)

        elif ok:
            self._set_load_status(usertypes.LoadStatus.warn)
        else:
            self._set_load_status(usertypes.LoadStatus.error)
        self.load_finished.emit(ok)
        if not self.title():
            self.title_changed.emit(self.url().toDisplayString())

    @pyqtSlot()
    def _on_history_trigger(self):
        """Emit add_history_item when triggered by backend-specific signal."""
        url = self.url()
        requested_url = self.url(requested=True)
        self.add_history_item.emit(url, requested_url, self.title())

    @pyqtSlot(int)
    def _on_load_progress(self, perc):
        self._progress = perc
        self.load_progress.emit(perc)

    @pyqtSlot()
    def _on_ssl_errors(self):
        self._has_ssl_errors = True

    def url(self, requested=False):
        raise NotImplementedError

    def progress(self):
        return self._progress

    def load_status(self):
        return self._load_status

    def _openurl_prepare(self, url):
        qtutils.ensure_valid(url)
        self.title_changed.emit(url.toDisplayString())

    def openurl(self, url):
        raise NotImplementedError

    def reload(self, *, force=False):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def clear_ssl_errors(self):
        raise NotImplementedError

    def dump_async(self, callback, *, plain=False):
        """Dump the current page to a file ascync.

        The given callback will be called with the result when dumping is
        complete.
        """
        raise NotImplementedError

    def run_js_async(self, code, callback=None):
        """Run javascript async.

        The given callback will be called with the result when running JS is
        complete.
        """
        raise NotImplementedError

    def run_js_blocking(self, code):
        """Run javascript and block.

        This returns the result to the caller. Its use should be avoided when
        possible as it runs a local event loop for QtWebEngine.
        """
        raise NotImplementedError

    def shutdown(self):
        raise NotImplementedError

    def title(self):
        raise NotImplementedError

    def icon(self):
        raise NotImplementedError

    def set_html(self, html, base_url):
        raise NotImplementedError

    def find_all_elements(self, selector, callback, *, only_visible=False):
        """Find all HTML elements matching a given selector async.

        Args:
            callback: The callback to be called when the search finished.
            selector: The CSS selector to search for.
            only_visible: Only show elements which are visible on screen.
        """
        raise NotImplementedError

    def find_focus_element(self, callback):
        """Find the focused element on the page async.

        Args:
            callback: The callback to be called when the search finished.
                      Called with a WebEngineElement or None.
        """
        raise NotImplementedError

    def __repr__(self):
        try:
            url = utils.elide(self.url().toDisplayString(QUrl.EncodeUnicode),
                              100)
        except AttributeError:
            url = '<AttributeError>'
        return utils.get_repr(self, tab_id=self.tab_id, url=url)
