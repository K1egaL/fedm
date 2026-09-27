"""PySide6 GUI для fedm. Русский интерфейс."""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    Qt, QThread, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QFont,
    QGuiApplication, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QTableView,
    QToolBar, QVBoxLayout,
)

from . import bench, config
from .bench import Result, Target
from .providers import PROTOCOL_LABEL, PROVIDERS

COLUMNS = ["Провайдер", "Протокол", "Адрес", "Медиана, мс", "P95, мс", "Потери, %"]

# Полупрозрачные цвета — работают и в светлой, и в тёмной теме
COLOR_FAST = QColor(76, 175, 80, 70)      # < 30 ms
COLOR_GOOD = QColor(139, 195, 74, 70)     # 30–80
COLOR_MED  = QColor(255, 235, 59, 80)     # 80–150
COLOR_SLOW = QColor(255, 152, 0, 80)      # 150–300
COLOR_BAD  = QColor(244, 67, 54, 80)      # > 300
COLOR_LOSS = QColor(158, 158, 158, 70)    # всё потеряно


def _fmt_ms(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def _color_for(r: Result) -> QColor | None:
    if r.median is None:
        return COLOR_LOSS if r.losses > 0 else None
    m = r.median
    if m < 30:  return COLOR_FAST
    if m < 80:  return COLOR_GOOD
    if m < 150: return COLOR_MED
    if m < 300: return COLOR_SLOW
    return COLOR_BAD


def _compute_winners(results: list[Result]) -> set[str]:
    """Ключи строк-победителей (лучшая медиана в каждом протоколе)."""
    best: dict[str, Result] = {}
    for r in results:
        if not r.samples:
            continue
        cur = best.get(r.target.protocol)
        if cur is None or (r.median or 9e9) < (cur.median or 9e9):
            best[r.target.protocol] = r
    return {r.target.key for r in best.values()}


# ----------------------------- рабочий поток ---------------------------


class Worker(QThread):
    progress = Signal(int, int)
    finished_ = Signal(object)   # list[Result] — object, чтобы не маршалить
    failed = Signal(str)

    def __init__(self, targets, domains, iterations, timeout, parent=None):
        super().__init__(parent)
        self._targets = targets
        self._domains = domains
        self._iterations = iterations
        self._timeout = timeout

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                def cb(done: int, total: int) -> None:
                    self.progress.emit(done, total)

                results = loop.run_until_complete(
                    bench.run_bench(
                        self._targets, self._domains,
                        self._iterations, self._timeout,
                        on_progress=cb,
                    )
                )
            finally:
                loop.close()
            self.finished_.emit(results)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


# ----------------------------- модель таблицы --------------------------


class ResultsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[Result] = []
        self._winners: set[str] = set()

    def set_results(self, results: list[Result]) -> None:
        self.beginResetModel()
        self._rows = results
        self.endResetModel()

    def set_winners(self, winners: set[str]) -> None:
        self.beginResetModel()
        self._winners = winners
        self.endResetModel()

    def result_at(self, row: int) -> Result:
        return self._rows[row]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()

        if role == Qt.DisplayRole:
            if c == 0: return r.target.provider
            if c == 1: return PROTOCOL_LABEL.get(r.target.protocol, r.target.protocol)
            if c == 2: return r.target.address
            if c == 3: return _fmt_ms(r.median)
            if c == 4: return _fmt_ms(r.p95)
            if c == 5: return f"{r.loss_pct:.1f}"

        if role == Qt.UserRole:
            if c == 3: return r.median if r.median is not None else float("inf")
            if c == 4: return r.p95 if r.p95 is not None else float("inf")
            if c == 5: return r.loss_pct

        if role == Qt.TextAlignmentRole and c >= 3:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        if role == Qt.BackgroundRole:
            col = _color_for(r)
            return QBrush(col) if col is not None else None

        if role == Qt.FontRole and r.target.key in self._winners:
            f = QFont()
            f.setBold(True)
            return f

        return None


class _SortProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._protocol: str | None = None

    def set_protocol(self, proto: str | None) -> None:
        self._protocol = proto
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        if self._protocol is None:
            return True
        src = self.sourceModel()
        r = src.result_at(row)
        return r.target.protocol == self._protocol

    def lessThan(self, left, right):
        lv = left.data(Qt.UserRole)
        rv = right.data(Qt.UserRole)
        if lv is not None and rv is not None:
            return lv < rv
        return super().lessThan(left, right)


# ----------------------------- диалоги ---------------------------------


class TextListDialog(QDialog):
    def __init__(self, title: str, hint: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 440)

        lay = QVBoxLayout(self)
        label = QLabel(hint)
        label.setWordWrap(True)
        lay.addWidget(label)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText("\n".join(items))
        lay.addWidget(self.edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("OK")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> list[str]:
        out: list[str] = []
        for line in self.edit.toPlainText().splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
        return out


# ----------------------------- главное окно ----------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fedm — быстрый тест DNS")
        self.resize(1150, 680)

        self.cfg = config.load()
        self._worker: Worker | None = None
        self._last_results: list[Result] = []

        self.statusBar()

        self._build_menu()
        self._build_toolbar()
        self._build_center()

        self._apply_theme(self.cfg.theme)
        self._update_status_idle()

    # ---------------------------- построение UI ----------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu("&Файл")
        act_export = QAction("Экспорт в JSON…", self)
        act_export.setShortcut(QKeySequence("Ctrl+S"))
        act_export.triggered.connect(self._export_json)
        m_file.addAction(act_export)

        act_csv = QAction("Экспорт в CSV…", self)
        act_csv.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_csv.triggered.connect(self._export_csv)
        m_file.addAction(act_csv)

        m_file.addSeparator()
        act_quit = QAction("Выход", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_set = mb.addMenu("&Настройки")
        act_prov = QAction("Провайдеры…", self)
        act_prov.triggered.connect(self._edit_providers)
        m_set.addAction(act_prov)
        act_custom = QAction("Свои серверы…", self)
        act_custom.triggered.connect(self._edit_custom)
        m_set.addAction(act_custom)
        act_domains = QAction("Тестовые домены…", self)
        act_domains.triggered.connect(self._edit_domains)
        m_set.addAction(act_domains)
        m_set.addSeparator()

        self.act_icmp = QAction("Включить ICMP-пинг", self, checkable=True)
        self.act_icmp.setChecked(self.cfg.enable_icmp)
        self.act_icmp.toggled.connect(self._toggle_icmp)
        m_set.addAction(self.act_icmp)

        m_theme = m_set.addMenu("Тема")
        grp = QActionGroup(self)
        for name, key in (("Системная", "auto"), ("Светлая", "light"), ("Тёмная", "dark")):
            a = QAction(name, self, checkable=True)
            a.setChecked(self.cfg.theme == key)
            a.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            grp.addAction(a)
            m_theme.addAction(a)

        m_help = mb.addMenu("&Справка")
        act_about = QAction("О программе", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Основная", self)
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_run = QAction("▶ Запуск", self)
        self.act_run.setShortcut(QKeySequence("F5"))
        self.act_run.triggered.connect(lambda _=False: self._run())
        tb.addAction(self.act_run)

        self.act_stop = QAction("■ Стоп", self)
        self.act_stop.setEnabled(False)
        self.act_stop.triggered.connect(lambda _=False: self._stop())
        tb.addAction(self.act_stop)

        tb.addSeparator()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setMinimumWidth(280)
        tb.addWidget(self.progress)

        tb.addSeparator()
        tb.addWidget(QLabel(" Протокол: "))
        self.proto_filter = QComboBox()
        self.proto_filter.addItem("Все протоколы", None)
        for key in ("plain", "dot", "doh", "doq", "icmp", "tcp"):
            self.proto_filter.addItem(PROTOCOL_LABEL[key], key)
        self.proto_filter.setMinimumWidth(160)
        self.proto_filter.currentIndexChanged.connect(self._on_filter_changed)
        tb.addWidget(self.proto_filter)

    def _build_center(self) -> None:
        self.model = ResultsModel(self)
        self.proxy = _SortProxy(self)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.setCentralWidget(self.table)

        self.best_label = QLabel("", self)
        self.best_label.setStyleSheet("font-family: monospace; padding: 4px;")
        self.best_label.setMargin(4)
        self.statusBar().addPermanentWidget(self.best_label, 1)

    # ---------------------------- тема ----------------------------

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if theme == "light":
            app.setStyleSheet(_QSS_LIGHT)
        elif theme == "dark":
            app.setStyleSheet(_QSS_DARK)
        else:
            app.setStyleSheet("")

    def _set_theme(self, theme: str) -> None:
        self.cfg.theme = theme
        config.save(self.cfg)
        self._apply_theme(theme)

    # ---------------------------- действия ----------------------------

    def _on_filter_changed(self, idx: int) -> None:
        self.proxy.set_protocol(self.proto_filter.itemData(idx))

    def _toggle_icmp(self, checked: bool) -> None:
        self.cfg.enable_icmp = checked
        config.save(self.cfg)

    def _edit_providers(self) -> None:
        dlg = ProviderDialog(self.cfg.selected_providers, self)
        if dlg.exec() == QDialog.Accepted:
            self.cfg.selected_providers = dlg.selected()
            config.save(self.cfg)
            self._update_status_idle()

    def _edit_custom(self) -> None:
        dlg = TextListDialog(
            "Свои DNS-серверы",
            "По одному на строку. Форматы:\n"
            "  1.1.1.1                            → обычный DNS (UDP:53)\n"
            "  tls://dns.example.com              → DoT (TLS:853)\n"
            "  https://dns.example.com/dns-query  → DoH (HTTPS)\n"
            "  quic://dns.example.com             → DoQ (QUIC)\n"
            "  tcp://host:443                     → TCP-подключение\n"
            "Строки, начинающиеся с #, игнорируются.",
            self.cfg.custom_targets, self,
        )
        if dlg.exec() == QDialog.Accepted:
            self.cfg.custom_targets = dlg.values()
            config.save(self.cfg)
            self._update_status_idle()

    def _edit_domains(self) -> None:
        dlg = TextListDialog(
            "Тестовые домены",
            "Домены, по которым проверяем резолверы.\n"
            "fedm подставляет случайный субдомен перед каждым запросом, "
            "чтобы обойти кэш резолвера и измерить реальную рекурсию.",
            self.cfg.domains, self,
        )
        if dlg.exec() == QDialog.Accepted:
            vals = dlg.values()
            if vals:
                self.cfg.domains = vals
                config.save(self.cfg)
                self._update_status_idle()

    def _about(self) -> None:
        QMessageBox.about(
            self, "О программе",
            "<b>fedm</b> v2.2 — быстрый тест DNS.<br><br>"
            "Измеряет реальную задержку резолверов: обычный DNS, DoT, DoH, DoQ. "
            "Использует случайные субдомены, чтобы обойти кэш.<br><br>"
            "github.com/K1egaL/fedm",
        )

    # ---------------------------- запуск ----------------------------

    def _run(self) -> None:
        targets = bench.build_targets(
            self.cfg.selected_providers,
            self.cfg.custom_targets,
            self.cfg.enable_icmp,
        )
        if not targets:
            QMessageBox.warning(
                self, "Нет целей",
                "Выберите хотя бы одного провайдера или добавьте свои серверы "
                "в Настройки → Свои серверы.",
            )
            return
        if not self.cfg.domains:
            QMessageBox.warning(self, "Нет доменов",
                                "Добавьте хотя бы один тестовый домен в Настройки.")
            return

        self.model.set_results([])
        self.model.set_winners(set())
        self.best_label.setText("")
        self.progress.setValue(0)
        self.act_run.setEnabled(False)
        self.act_stop.setEnabled(True)
        self.statusBar().showMessage(
            f"Идёт тест: {len(targets)} целей × {len(self.cfg.domains)} доменов "
            f"× {self.cfg.iterations} итераций…"
        )

        self._worker = Worker(
            targets, self.cfg.domains,
            self.cfg.iterations, self.cfg.timeout, self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1500)
            self._worker = None
            self.act_run.setEnabled(True)
            self.act_stop.setEnabled(False)
            self.statusBar().showMessage("Остановлено.")
            self.progress.setValue(0)

    def _on_progress(self, done: int, total: int) -> None:
        pct = 0 if total == 0 else int(100 * done / total)
        self.progress.setValue(pct)
        self.statusBar().showMessage(f"{done}/{total} запросов…")

    def _on_finished(self, results: list[Result]) -> None:
        self._worker = None
        self.act_run.setEnabled(True)
        self.act_stop.setEnabled(False)
        self.progress.setValue(100)

        def sort_key(r: Result):
            m = r.median if r.median is not None else float("inf")
            return (m, r.loss_pct)

        results = sorted(results, key=sort_key)
        self._last_results = results
        self.model.set_results(results)
        self.model.set_winners(_compute_winners(results))

        # автосортировка по медиане (возр.) — самые быстрые сверху
        self.table.sortByColumn(3, Qt.AscendingOrder)
        self.table.resizeColumnsToContents()

        self.statusBar().showMessage(f"Готово — строк: {len(results)}.")
        self._update_best_label(results)

    def _on_failed(self, msg: str) -> None:
        self._worker = None
        self.act_run.setEnabled(True)
        self.act_stop.setEnabled(False)
        self.statusBar().showMessage("Ошибка.")
        QMessageBox.critical(self, "Тест упал", msg)

    def _update_best_label(self, results: list[Result]) -> None:
        best: dict[str, Result] = {}
        for r in results:
            if not r.samples:
                continue
            cur = best.get(r.target.protocol)
            if cur is None or (r.median or 9e9) < (cur.median or 9e9):
                best[r.target.protocol] = r
        if not best:
            self.best_label.setText("")
            return
        lines = []
        order = ("plain", "dot", "doh", "doq", "icmp", "tcp")
        for proto in order:
            r = best.get(proto)
            if r is None:
                continue
            name = f"{r.target.provider} {r.target.address}"
            label = PROTOCOL_LABEL.get(proto, proto)
            lines.append(f"★ {label}: {name} — {r.median:.1f} мс")
        self.best_label.setText("   |   ".join(lines))

    def _update_status_idle(self) -> None:
        n = len(self.cfg.selected_providers)
        c = len(self.cfg.custom_targets)
        d = len(self.cfg.domains)
        self.statusBar().showMessage(
            f"Провайдеров: {n} • Своих: {c} • Доменов: {d} • "
            f"Итераций: {self.cfg.iterations} • Таймаут: {self.cfg.timeout} с"
        )

    # ---------------------------- экспорт ----------------------------

    def _export_json(self) -> None:
        if not self._last_results:
            QMessageBox.information(self, "Нечего экспортировать",
                                    "Сначала запустите тест.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт результатов",
            str(Path.home() / "fedm-results.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        payload = {
            "iterations": self.cfg.iterations,
            "timeout": self.cfg.timeout,
            "domains": self.cfg.domains,
            "results": [
                {
                    "provider": r.target.provider,
                    "protocol": r.target.protocol,
                    "address": r.target.address,
                    "median_ms": r.median,
                    "p95_ms": r.p95,
                    "loss_pct": r.loss_pct,
                    "samples_ms": r.samples,
                }
                for r in self._last_results
            ],
        }
        try:
            Path(path).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            QMessageBox.critical(self, "Экспорт не удался", str(e))
            return
        self.statusBar().showMessage(f"Сохранено → {path}")

    def _export_csv(self) -> None:
        if not self._last_results:
            QMessageBox.information(self, "Нечего экспортировать",
                                    "Сначала запустите тест.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт результатов (CSV)",
            str(Path.home() / "fedm-results.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Провайдер", "Протокол", "Адрес",
                            "Медиана мс", "P95 мс", "Потери %", "Замеров"])
                for r in self._last_results:
                    w.writerow([
                        r.target.provider,
                        r.target.protocol,
                        r.target.address,
                        "" if r.median is None else f"{r.median:.2f}",
                        "" if r.p95 is None else f"{r.p95:.2f}",
                        f"{r.loss_pct:.1f}",
                        len(r.samples),
                    ])
        except OSError as e:
            QMessageBox.critical(self, "Экспорт не удался", str(e))
            return
        self.statusBar().showMessage(f"Сохранено → {path}")

    def _copy_markdown(self) -> None:
        if not self._last_results:
            QMessageBox.information(self, "Нечего копировать",
                                    "Сначала запустите тест.")
            return
        lines = [
            "| Провайдер | Протокол | Адрес | Медиана, мс | P95, мс | Потери, % |",
            "|---|---|---|---:|---:|---:|",
        ]
        for r in self._last_results:
            lines.append(
                f"| {r.target.provider} | "
                f"{PROTOCOL_LABEL.get(r.target.protocol, r.target.protocol)} | "
                f"`{r.target.address}` | "
                f"{_fmt_ms(r.median)} | {_fmt_ms(r.p95)} | {r.loss_pct:.1f} |"
            )
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage(
            f"Скопировано как Markdown ({len(self._last_results)} строк)", 3000
        )

    # ---------------------------- контекстное меню ----------------------------

    def _on_context_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        src_row = self.proxy.mapToSource(idx).row()
        r = self.model.result_at(src_row)
        t = r.target

        menu = QMenu(self)

        ip = self._ip_for(t)
        a_ip = QAction(f"Копировать IP — {ip}" if ip else "Копировать IP", self)
        a_ip.setEnabled(bool(ip))
        a_ip.triggered.connect(lambda: self._copy(ip))
        menu.addAction(a_ip)

        doh = self._endpoint_for(t, "doh")
        a_doh = QAction(f"Копировать DoH — {doh}" if doh else "Копировать DoH", self)
        a_doh.setEnabled(bool(doh))
        a_doh.triggered.connect(lambda: self._copy(doh))
        menu.addAction(a_doh)

        doq = self._endpoint_for(t, "doq")
        a_doq = QAction(f"Копировать DoQ — {doq}" if doq else "Копировать DoQ", self)
        a_doq.setEnabled(bool(doq))
        a_doq.triggered.connect(lambda: self._copy(doq))
        menu.addAction(a_doq)

        menu.addSeparator()
        a_raw = QAction(f"Копировать адрес — {t.address}", self)
        a_raw.triggered.connect(lambda: self._copy(t.address))
        menu.addAction(a_raw)

        menu.addSeparator()
        a_md = QAction("📋  Копировать всю таблицу как Markdown", self)
        a_md.triggered.connect(self._copy_markdown)
        menu.addAction(a_md)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy(self, text: str | None) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Скопировано: {text}", 2500)

    @staticmethod
    def _ip_for(t: Target) -> str | None:
        if t.protocol in ("plain", "icmp", "dot"):
            return t.address
        if t.protocol == "doh":
            return urlparse(t.address).hostname
        if t.protocol == "doq":
            return t.address
        if t.protocol == "tcp":
            return t.address.split(":", 1)[0]
        return None

    @staticmethod
    def _endpoint_for(t: Target, proto: str) -> str | None:
        if t.protocol == proto:
            return t.address
        data = PROVIDERS.get(t.provider)
        if not data:
            return None
        vals = data.get(proto) or []
        return vals[0] if vals else None


# ----------------------------- выбор провайдеров -------------------------


class ProviderDialog(QDialog):
    def __init__(self, selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Провайдеры")
        self.resize(380, 460)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Выберите DNS-провайдеров для теста:"))

        self.list = QListWidget()
        for name in PROVIDERS.keys():
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if name in selected else Qt.Unchecked
            )
            self.list.addItem(item)
        lay.addWidget(self.list, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        btns.button(QDialogButtonBox.Ok).setText("OK")
        btns.button(QDialogButtonBox.Cancel).setText("Отмена")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected(self) -> list[str]:
        out: list[str] = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.text())
        return out


# ----------------------------- темы -------------------------------------

_QSS_LIGHT = """
QTableView {
    gridline-color: #dcdcdc;
    selection-background-color: #cfe4ff;
    selection-color: #000;
}
QHeaderView::section {
    background: #f3f3f3;
    padding: 4px 8px;
    border: 0;
    border-right: 1px solid #e0e0e0;
    border-bottom: 1px solid #e0e0e0;
}
"""

_QSS_DARK = """
QWidget { background: #23262b; color: #e6e6e6; }
QMenuBar, QMenu, QToolBar, QStatusBar { background: #1e2024; }
QTableView {
    background: #23262b;
    alternate-background-color: #26292f;
    gridline-color: #33373d;
    selection-background-color: #3a4a63;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #1e2024;
    color: #e6e6e6;
    padding: 4px 8px;
    border: 0;
    border-right: 1px solid #33373d;
    border-bottom: 1px solid #33373d;
}
QProgressBar {
    background: #1e2024;
    border: 1px solid #33373d;
    border-radius: 3px;
    text-align: center;
}
QProgressBar::chunk { background: #4a86e8; }
QPlainTextEdit, QListWidget, QComboBox {
    background: #1e2024;
    border: 1px solid #33373d;
}
"""


# ----------------------------- точка входа -----------------------------


def _excepthook(tp, value, tb):
    import traceback
    traceback.print_exception(tp, value, tb)


def main() -> int:
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("fedm")
    app.setOrganizationName("fedm")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())