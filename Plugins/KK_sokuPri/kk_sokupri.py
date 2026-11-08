# 数値計算とファイル操作に必要な標準機能を読み込む。
import math
import os

from qgis.PyQt.QtCore import Qt, QCoreApplication, QSize, QSizeF, QRect, QMarginsF, QPoint
from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPageLayout, QPageSize, QPainter, QPainterPath, QPen
from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtWidgets import (
    QAction, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QSpinBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget
)

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsDistanceArea, QgsMapRendererCustomPainterJob,
    QgsGeometry, QgsMapSettings, QgsMessageLog, QgsPointXY, QgsProject, QgsRectangle,
    QgsScaleUtils, QgsUnitTypes
)
from qgis.gui import QgsMapTool, QgsRubberBand


def enum_value(owner, *names, default=None):
    """QGISのバージョン差を吸収して列挙値を取得する。"""
    for name in names:
        value = owner
        try:
            for part in name.split("."):
                value = getattr(value, part)
            return value
        except AttributeError:
            continue
    return default


def qt_enum(owner, *names, default=None):
    """Qtの列挙値を新旧両方の書き方に対応して取得する。"""
    try:
        return enum_value(owner, *names, default=default)
    except AttributeError:
        try:
            return enum_value(Qt, *names, default=default)
        except AttributeError:
            return default


def log_exception(message, error):
    """エラー内容をQGISのログへ記録する。"""
    QgsMessageLog.logMessage(
        "{}: {}".format(message, error),
        "KK_sokuPri",
        Qgis.MessageLevel.Critical
    )


PAPER_SIZES_MM = {
    # 用紙名を幅・高さ（ミリメートル）へ変換するための一覧。
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "B5": (176.0, 250.0),
    "B4": (250.0, 353.0),
    "B3": (353.0, 500.0),
    "B2": (500.0, 707.0),
    "B1": (707.0, 1000.0),
}

PT_TO_MM = 25.4 / 72.0
# 印刷時の余白も単位を揃えるため、ポイントからミリメートルへ変換する。
SIDE_MARGIN_MM = 20.0 * PT_TO_MM
TOP_MARGIN_MM = 40.0 * PT_TO_MM
BOTTOM_MARGIN_MM = 20.0 * PT_TO_MM


class PrintPreviewMapTool(QgsMapTool):
    """地図上で印刷範囲を選ぶためのマウス操作を処理するクラス。"""
    def __init__(self, canvas, plugin):
        super().__init__(canvas)
        self.plugin = plugin
        self._center = None
        self.rubber_band = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
        self.rubber_band.setStrokeColor(QColor("#000000"))
        self.rubber_band.setFillColor(QColor(255, 255, 255, 102))
        self.rubber_band.setWidth(2)
        self.setCursor(qt_enum(Qt, "CursorShape.CrossCursor", "CrossCursor"))

    def canvasMoveEvent(self, event):
        # マウスを動かすと、カーソル位置を印刷範囲の中心として枠を更新する。
        point = self.toMapCoordinates(event.pos())
        self._center = point
        self._update_preview(point)

    def canvasPressEvent(self, event):
        # 左クリックされた位置で確認画面を表示し、OKならPDFを作成する。
        if event.button() != qt_enum(Qt, "MouseButton.LeftButton", "LeftButton"):
            return
        center = self.toMapCoordinates(event.pos())
        if self.plugin.confirm_print(center):
            self.plugin.print_at(center)

    def _update_preview(self, center):
        # 印刷範囲の四角形を地図キャンバス上に赤線で表示する。
        rectangle = self.plugin.sheet_rectangle(center)
        if rectangle is None:
            self.rubber_band.hide()
            return
        polygon = self.plugin.preview_geometry(rectangle, center)
        self.rubber_band.setToGeometry(polygon, None)
        self.rubber_band.show()

    def activate(self):
        super().activate()
        if self._center is not None:
            self._update_preview(self._center)

    def deactivate(self):
        self.rubber_band.hide()
        super().deactivate()

    def refresh(self):
        if self._center is not None:
            self._update_preview(self._center)


class KK_sokuPriDock(QWidget):
    """プラグインの設定パネルに表示する入力項目を作るクラス。"""
    settings_changed = None

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        # タイトル文字列を入力する欄を作る。
        from qgis.PyQt.QtWidgets import QLineEdit
        self.title_edit = QLineEdit()
        layout.addRow("タイトル", self.title_edit)

        self.font_combo = QComboBox()
        self.font_combo.setEditable(False)
        try:
            # QGISに登録されたフォント一覧を取得し、選択ボックスへ追加する。
            font_database = QFontDatabase()
            font_families = font_database.families()
        except TypeError:
            font_families = QFontDatabase.families()
        self.font_combo.addItems(font_families)
        general_font = qt_enum(
            QFontDatabase,
            "SystemFont.GeneralFont",
            "GeneralFont",
            default=0
        )
        self.font_combo.setCurrentText(QFontDatabase.systemFont(general_font).family())
        layout.addRow("フォント", self.font_combo)

        self.scale_combo = QComboBox()
        self.scale_combo.setEditable(True)
        scales = []
        try:
            scales = QgsScaleUtils.scaleList()
        except AttributeError:
            project = QgsProject.instance()
            scales = project.readListEntry("ScaleBar", "/PredefinedScales", [])[0]
        if not scales:
            # プロジェクトに縮尺がない場合は、よく使われる縮尺を用意する。
            scales = [
                500, 1000, 2000, 2500, 5000, 10000, 20000, 25000,
                50000, 100000, 250000, 500000, 1000000
            ]
        scale_values = []
        for scale in scales:
            try:
                scale_text = str(scale).strip().replace("1:", "").replace(",", "")
                scale_values.append("{:d}".format(int(float(scale_text))))
            except (TypeError, ValueError):
                continue
        self.scale_combo.addItems(sorted(set(scale_values), key=int))
        self.scale_combo.setEditText("50000")
        layout.addRow("縮尺", self.scale_combo)

        # 用紙サイズと方向を選択する欄を作る。
        self.paper_combo = QComboBox()
        self.paper_combo.addItems(list(PAPER_SIZES_MM.keys()))
        self.paper_combo.setCurrentText("A4")
        layout.addRow("用紙サイズ", self.paper_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["縦", "横"])
        layout.addRow("用紙方向", self.orientation_combo)

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setValue(300)
        self.dpi_spin.setSuffix(" dpi")
        layout.addRow("解像度", self.dpi_spin)

        from qgis.PyQt.QtWidgets import QLineEdit
        self.copyright_edit = QLineEdit()
        layout.addRow("著作権ラベル", self.copyright_edit)

        layout.addRow("", QLabel("地図をクリックするとPDFに保存します。"))
        layout.addRow("", QLabel("用紙の下に、スケールバー、縮尺、著作権ラベルを表示します。"))
        layout.addRow("", QLabel("枠は用紙の範囲です。地図は余白分小さく印刷されます。"))



class KK_sokuPri:
    """QGISへプラグインを登録し、PDF印刷処理全体を管理するクラス。"""
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "&KK_sokuPri"
        self.toolbar = self.iface.addToolBar("KK_sokuPri")
        self.toolbar.setObjectName("KK_sokuPri")

        self.dock = None
        self.dock_widget = None
        self.preview_tool = None

    def initGui(self):
        # プラグイン起動時に、メニューとツールバーへボタンを追加する。
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path)
        action = QAction(icon, "KK_sokuPri 即PDFプリント", self.iface.mainWindow())
        action.triggered.connect(self.toggle_dock)
        self.iface.addPluginToMenu(self.menu, action)
        self.toolbar.addAction(action)
        self.actions.append(action)

    def unload(self):
        # プラグイン終了時に、追加したツールやパネルを取り除く。
        self.stop_preview()
        if self.preview_tool is not None:
            try:
                self.iface.mapCanvas().setMapTool(None)
            except (AttributeError, RuntimeError) as error:
                log_exception("地図ツールを解除できませんでした", error)
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
            self.toolbar.removeAction(action)
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)

    def tr(self, message):
        return QCoreApplication.translate("KK_sokuPri", message)

    def toggle_dock(self):
        # 設定パネルが未作成なら作成し、すでにあるなら表示・非表示を切り替える。
        if self.dock_widget is None:
            self.dock = KK_sokuPriDock()
            self.dock.title_edit.textChanged.connect(self.refresh_preview)
            self.dock.font_combo.currentTextChanged.connect(self.refresh_preview)
            self.dock.scale_combo.currentTextChanged.connect(self.refresh_preview)
            self.dock.scale_combo.lineEdit().textChanged.connect(self.refresh_preview)
            self.dock.paper_combo.currentTextChanged.connect(self.refresh_preview)
            self.dock.orientation_combo.currentTextChanged.connect(self.refresh_preview)
            self.dock.dpi_spin.valueChanged.connect(self.refresh_preview)
            self.dock.copyright_edit.textChanged.connect(self.refresh_preview)
            self._load_project_copyright()

            self.dock_widget = QDockWidget("即PDFプリント", self.iface.mainWindow())
            self.dock_widget.setWidget(self.dock)
            self.dock_widget.setObjectName("KK_sokuPriDock")
            self.dock_widget.visibilityChanged.connect(self._dock_visibility_changed)
            self.iface.addDockWidget(            qt_enum(Qt, "DockWidgetArea.RightDockWidgetArea", "RightDockWidgetArea"), self.dock_widget)

            self.preview_tool = PrintPreviewMapTool(self.iface.mapCanvas(), self)
            self.start_preview()
        else:
            self.dock_widget.setVisible(not self.dock_widget.isVisible())

    def _load_project_copyright(self):
        # 著作権表示に使う文字を、地図デコレーションやプロジェクト設定から探す。
        project = QgsProject.instance()
        copyright_text = ""

        try:
            for decoration in self.iface.mapCanvas().decorations():
                if "Copyright" in decoration.__class__.__name__:
                    for method_name in ("text", "copyrightText", "label", "labelText"):
                        method = getattr(decoration, method_name, None)
                        if callable(method):
                            value = method()
                            if value:
                                copyright_text = str(value)
                                break
                    if copyright_text:
                        break
        except (AttributeError, TypeError) as error:
            log_exception("著作権デコレーションを読み込めませんでした", error)

        if not copyright_text:
            try:
                from qgis.PyQt.QtXml import QDomDocument
                document = QDomDocument()
                project.write(document)
                node = document.documentElement().firstChild()
                while not node.isNull():
                    element = node.toElement()
                    node_name = element.tagName().lower()
                    if "copyright" in node_name or node_name == "decorations":
                        copyright_text = self._copyright_text_from_xml_element(element)
                        if copyright_text:
                            break
                    node = node.nextSibling()
            except Exception as error:
                log_exception("プロジェクトXMLから著作権情報を読み込めませんでした", error)

        candidates = (
            ("Copyright", "/Text"),
            ("Copyright", "/text"),
            ("Copyright", "Text"),
            ("Copyright", "text"),
            ("Decorations", "/Copyright/Text"),
            ("Decorations", "/Copyright/text"),
        )
        for section, key in candidates:
            value = project.readEntry(section, key, "")[0]
            if value:
                copyright_text = value
                break
        self.dock.copyright_edit.setText(copyright_text)

    def _copyright_text_from_xml_element(self, element):
        # プロジェクトのXML内を再帰的に調べて著作権文字列を探す。
        if element.isNull():
            return ""
        for attribute_name in ("text", "label", "value"):
            value = element.attribute(attribute_name, "")
            if value:
                return value
        child = element.firstChild()
        while not child.isNull():
            child_element = child.toElement()
            if not child_element.isNull():
                name = child_element.attribute("name", "").lower()
                if name in ("text", "label", "labeltext"):
                    value = child_element.attribute("value", child_element.text())
                    if value:
                        return value
                if child_element.tagName().lower() in ("text", "label", "labeltext"):
                    value = child_element.attribute("value", child_element.text())
                    if value:
                        return value
                value = self._copyright_text_from_xml_element(child_element)
                if value:
                    return value
            child = child.nextSibling()
        return ""

    def _dock_visibility_changed(self, visible):
        # 設定パネルが表示されたら地図ツールを有効化し、隠れたら解除する。
        if visible:
            self.start_preview()
        else:
            self.stop_preview()

    def start_preview(self):
        if self.preview_tool is not None:
            self.iface.mapCanvas().setMapTool(self.preview_tool)

    def stop_preview(self):
        if self.preview_tool is not None:
            self.preview_tool.deactivate()
            self.iface.mapCanvas().unsetMapTool(self.preview_tool)
            self.iface.mapCanvas().setCursor(qt_enum(Qt, "CursorShape.ArrowCursor", "ArrowCursor"))

    def refresh_preview(self, *_args):
        # 設定が変更されたら印刷範囲のプレビューを再描画する。
        if self.preview_tool is not None:
            self.preview_tool.refresh()

    def paper_size(self):
        # 選択された用紙サイズを、縦・横の向きを反映した寸法で返す。
        width, height = PAPER_SIZES_MM[self.dock.paper_combo.currentText()]
        if self.dock.orientation_combo.currentText() == "横":
            width, height = height, width
        return width, height

    def scale_denominator(self):
        # 入力欄の縮尺文字を数値へ変換する。無効な値なら既定値を使う。
        text = self.dock.scale_combo.currentText().replace(",", "").strip()
        try:
            scale = float(text)
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError
            return max(1.0, scale)
        except (TypeError, ValueError):
            QMessageBox.warning(self.iface.mainWindow(), "KK_sokuPri", "縮尺には数値を入力してください。")
            return 50000.0

    def print_dpi(self):
        # 印刷解像度を72〜1200 dpiの範囲に収める。
        return max(72, min(1200, int(self.dock.dpi_spin.value())))

    def meters_per_crs_unit(self):
        # 地図座標系の1単位が何メートルかを求める。
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        try:
            distance_meters = QgsUnitTypes.DistanceUnit.DistanceMeters
            factor = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), distance_meters)
            if factor > 0:
                return factor
        except (AttributeError, TypeError):
            pass

        center = self.iface.mapCanvas().extent().center()
        da = QgsDistanceArea()
        da.setSourceCrs(crs, QgsProject.instance().transformContext())
        try:
            da.setEllipsoid(QgsProject.instance().ellipsoid())
        except AttributeError:
            pass
        if crs.mapUnits() == QgsUnitTypes.DistanceUnit.DistanceDegrees:
            from qgis.core import QgsPointXY
            meter_distance = da.measureLine(
                QgsPointXY(center.x() - 0.5, center.y()),
                QgsPointXY(center.x() + 0.5, center.y())
            )
            return meter_distance / 1.0
        return 1.0

    def sheet_extent(self, center):
        # 用紙に収まる地図範囲を、選択された縮尺から計算する。
        try:
            width_mm, height_mm = self.paper_size()
            scale = self.scale_denominator()
            map_width_mm = width_mm - 2.0 * SIDE_MARGIN_MM
            map_height_mm = height_mm - TOP_MARGIN_MM - BOTTOM_MARGIN_MM - 28.0 * PT_TO_MM
            map_width_m = map_width_mm * scale / 1000.0
            map_height_m = map_height_mm * scale / 1000.0

            unit_factor = self.meters_per_crs_unit()
            if not unit_factor or unit_factor <= 0:
                unit_factor = 1.0
            width_units = map_width_m / unit_factor
            height_units = map_height_m / unit_factor

            return QgsRectangle(
                center.x() - width_units / 2.0,
                center.y() - height_units / 2.0,
                center.x() + width_units / 2.0,
                center.y() + height_units / 2.0
            )
        except (AttributeError, TypeError, ValueError, OverflowError) as error:
            log_exception("地図範囲を計算できませんでした", error)
            return None

    def sheet_rectangle(self, center):
        # プレビュー用の、用紙全体に近い四角形を計算する。
        map_extent = self.sheet_extent(center)
        if map_extent is None:
            return None
        width_mm, height_mm = self.paper_size()
        map_width_mm = width_mm - 2.0 * SIDE_MARGIN_MM
        unit_factor = self.meters_per_crs_unit()
        full_width_units = map_width_mm * self.scale_denominator() / 1000.0 / unit_factor
        full_height_units = full_width_units * height_mm / width_mm
        return QgsRectangle(
            center.x() - full_width_units / 2.0,
            center.y() - full_height_units / 2.0,
            center.x() + full_width_units / 2.0,
            center.y() + full_height_units / 2.0
        )

    def preview_geometry(self, rectangle, center):
        # 地図の回転に対応して、印刷範囲を画面上の四角形へ変換する。
        canvas_settings = self.iface.mapCanvas().mapSettings()
        unrotated_settings = QgsMapSettings(canvas_settings)
        unrotated_settings.setRotation(0.0)

        unrotated_transform = unrotated_settings.mapToPixel()
        canvas_transform = canvas_settings.mapToPixel()

        center_pixel = unrotated_transform.transform(center)
        rectangle_corners = (
            QgsPointXY(rectangle.xMinimum(), rectangle.yMinimum()),
            QgsPointXY(rectangle.xMaximum(), rectangle.yMinimum()),
            QgsPointXY(rectangle.xMaximum(), rectangle.yMaximum()),
            QgsPointXY(rectangle.xMinimum(), rectangle.yMaximum()),
        )
        projected = [unrotated_transform.transform(point) for point in rectangle_corners]

        left = min(point.x() for point in projected)
        right = max(point.x() for point in projected)
        bottom = min(point.y() for point in projected)
        top = max(point.y() for point in projected)

        screen_corners = (
            QPoint(round(center_pixel.x() - (right - left) / 2.0), round(center_pixel.y() - (top - bottom) / 2.0)),
            QPoint(round(center_pixel.x() + (right - left) / 2.0), round(center_pixel.y() - (top - bottom) / 2.0)),
            QPoint(round(center_pixel.x() + (right - left) / 2.0), round(center_pixel.y() + (top - bottom) / 2.0)),
            QPoint(round(center_pixel.x() - (right - left) / 2.0), round(center_pixel.y() + (top - bottom) / 2.0)),
        )
        map_corners = [
            canvas_transform.toMapCoordinates(point)
            for point in screen_corners
        ]
        map_corners.append(map_corners[0])
        return QgsGeometry.fromPolygonXY([map_corners])

    def confirm_print(self, center):
        # PDFを作成する前に、ユーザーへ確認ダイアログを表示する。
        answer = QMessageBox.question(
            self.iface.mainWindow(),
            "KK_sokuPri",
            "この範囲をPDFに出力しますか？",
            qt_enum(QMessageBox, "StandardButton.Yes", "Yes") |
            qt_enum(QMessageBox, "StandardButton.No", "No"),
            qt_enum(QMessageBox, "StandardButton.Yes", "Yes")
        )
        return answer == qt_enum(QMessageBox, "StandardButton.Yes", "Yes")

    def print_at(self, center):
        # PDF作成処理を呼び出し、失敗した場合は利用者へ通知する。
        try:
            self._print_to_pdf(center)
        except Exception as error:
            log_exception("PDFの作成中にエラーが発生しました", error)
            QMessageBox.critical(self.iface.mainWindow(), "KK_sokuPri", "PDFを作成できませんでした。\n{}".format(error))

    def _printer_orientation(self):
        # Qtの用紙方向設定へ、選択した「縦」または「横」を変換する。
        if self.dock.orientation_combo.currentText() == "横":
            return qt_enum(QPageLayout, "Orientation.Landscape", "Landscape", default=1)
        return qt_enum(QPageLayout, "Orientation.Portrait", "Portrait", default=0)

    def _printer_page_size(self):
        # 選択された用紙サイズを、QPrinterで使用する形式へ変換する。
        page_size_id = qt_enum(
            QPageSize,
            "PageSizeId.{}".format(self.dock.paper_combo.currentText()),
            self.dock.paper_combo.currentText()
        )
        if page_size_id is None:
            width_mm, height_mm = PAPER_SIZES_MM[self.dock.paper_combo.currentText()]
            return QPageSize(
                QSizeF(width_mm, height_mm),
                qt_enum(QPageSize, "Unit.Millimeter", "Millimeter"),
                "",
                qt_enum(QPageSize, "SizeMatchPolicy.ExactMatch", "ExactMatch")
            )
        return QPageSize(page_size_id)

    def _print_to_pdf(self, center):
        # タイトル、地図、方位記号、縮尺を配置してPDFを作成する。
        width_mm, height_mm = self.paper_size()
        default_name = "KK_sokuPri.pdf"
        if self.dock.title_edit.text().strip():
            default_name = "{}.pdf".format(self.dock.title_edit.text().strip())

        file_path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            "PDFを保存",
            default_name,
            "PDFファイル (*.pdf)"
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        printer = QPrinter(qt_enum(QPrinter, "PrinterMode.HighResolution", "HighResolution"))
        printer.setFullPage(True)
        printer.setOutputFormat(qt_enum(QPrinter, "OutputFormat.PdfFormat", "PdfFormat"))
        printer.setOutputFileName(file_path)
        printer.setResolution(self.print_dpi())
        page_layout = QPageLayout(
            self._printer_page_size(),
            self._printer_orientation(),
            QMarginsF(0.0, 0.0, 0.0, 0.0),
            qt_enum(QPageLayout, "Unit.Millimeter", "Millimeter")
        )
        printer.setPageLayout(page_layout)

        painter = QPainter()
        try:
            if not painter.begin(printer):
                raise RuntimeError("PDF出力を開始できませんでした。")

            # タイトルを紙面の上部中央へ20ptで描画する。
            title = self.dock.title_edit.text().strip()
            title_font = QFont(self.dock.font_combo.currentText())
            title_font.setPointSize(20)
            painter.setFont(title_font)
            painter.setPen(qt_enum(Qt, "GlobalColor.black", "black"))
            title_y = int((8.0 - 10.0 * PT_TO_MM) * printer.resolution() / 25.4)
            painter.drawText(
                0,
                title_y,
                printer.width(),
                int(printer.resolution() / 25.4 * 14.0),
                qt_enum(Qt, "AlignmentFlag.AlignHCenter", "AlignHCenter") |
                qt_enum(Qt, "AlignmentFlag.AlignTop", "AlignTop"),
                title
            )

            map_x = SIDE_MARGIN_MM / 25.4 * printer.resolution()
            map_y = TOP_MARGIN_MM / 25.4 * printer.resolution()
            map_width = (width_mm - 2.0 * SIDE_MARGIN_MM) / 25.4 * printer.resolution()
            map_height = (height_mm - TOP_MARGIN_MM - BOTTOM_MARGIN_MM - 28.0 * PT_TO_MM) / 25.4 * printer.resolution()

            # 現在の地図表示設定をコピーし、印刷範囲とサイズを上書きする。
            map_settings = QgsMapSettings(self.iface.mapCanvas().mapSettings())
            map_settings.setExtent(self.sheet_extent(center))
            map_settings.setOutputSize(QSize(int(map_width), int(map_height)))
            map_settings.setOutputDpi(printer.resolution())
            map_settings.setDestinationCrs(self.iface.mapCanvas().mapSettings().destinationCrs())
            map_settings.setLayers(self.iface.mapCanvas().mapSettings().layers())
            try:
                map_settings.setRotation(float(self.iface.mapCanvas().mapSettings().rotation()))
            except (AttributeError, TypeError, ValueError):
                pass

            image_format = qt_enum(
                QImage,
                "Format.Format_ARGB32_Premultiplied",
                "Format_ARGB32_Premultiplied",
                default=None
            )
            if image_format is None:
                raise RuntimeError("PDF用地図画像の形式を取得できませんでした。")
            # 地図画像を一時的に作成し、レイヤーを描画する。
            map_image = QImage(int(map_width), int(map_height), image_format)
            map_image.fill(QColor("#ffffff"))
            image_painter = QPainter(map_image)
            try:
                job = QgsMapRendererCustomPainterJob(map_settings, image_painter)
                job.start()
                job.waitForFinished()
                if hasattr(job, "renderingErrors"):
                    rendering_errors = job.renderingErrors()
                    if rendering_errors:
                        raise RuntimeError(
                            "地図の描画に失敗しました: {}".format("; ".join(map(str, rendering_errors)))
                        )
            finally:
                image_painter.end()

            jpeg_path = "{}.map.jpg".format(os.path.splitext(file_path)[0])
            try:
                # PDFへ埋め込むため、一時JPEG画像として保存する。
                if not map_image.save(jpeg_path, "JPG", 90):
                    raise RuntimeError("PDF用地図画像をJPEGに変換できませんでした。")
                jpeg_image = QImage(jpeg_path)
                if jpeg_image.isNull():
                    raise RuntimeError("JPEGに変換したPDF用地図画像を読み込めませんでした。")
                painter.drawImage(
                    int(map_x),
                    int(map_y),
                    jpeg_image.scaled(
                        int(map_width),
                        int(map_height),
                        qt_enum(Qt, "AspectRatioMode.IgnoreAspectRatio", "IgnoreAspectRatio"),
                        qt_enum(Qt, "TransformationMode.SmoothTransformation", "SmoothTransformation")
                    )
                )
            finally:
                if os.path.isfile(jpeg_path):
                    os.remove(jpeg_path)
            frame_width = max(1, int(round(0.3 / 25.4 * printer.resolution())))
            painter.setPen(QPen(QColor("#000000"), frame_width, qt_enum(Qt, "PenStyle.SolidLine", "SolidLine", default=1)))
            painter.setBrush(qt_enum(Qt, "BrushStyle.NoBrush", "NoBrush"))
            painter.drawRect(int(map_x), int(map_y), int(map_width), int(map_height))
            # 地図枠の中に方位記号を、下側に縮尺と著作権表示を追加する。
            self.draw_north_arrow(painter, map_x, map_y, map_width, map_height, printer.resolution())
            self.draw_scale_elements(painter, map_x, map_y + map_height, map_width, printer.resolution())
            self.draw_project_copyright(painter, map_x, map_y + map_height, map_width, printer.resolution())
        finally:
            if painter.isActive():
                painter.end()
        if not os.path.isfile(file_path) or os.path.getsize(file_path) <= 0:
            raise RuntimeError("PDFファイルを保存できませんでした。出力先のアクセス権を確認してください。")
        QMessageBox.information(self.iface.mainWindow(), "KK_sokuPri", "PDFを作成しました。\n{}".format(file_path))

    def draw_north_arrow(self, painter, map_x, map_y, map_width, map_height, dpi):
        # 地図右上へ、地図の回転に合わせた青色の方位記号を描く。
        mm = dpi / 25.4
        scale = 0.70
        arrow_width = int(15.0 * mm * scale)
        arrow_height = int(20.0 * mm * scale)
        inset = int(2.0 * mm)
        center_x = int(map_x + map_width - inset - arrow_width / 2.0)
        top_y = int(map_y + inset + 10.0 * dpi / 72.0)
        ring_center_y = int(top_y + 12.0 * mm * scale)
        outer_radius = int(7.0 * mm * scale)

        painter.save()
        clip_operation = qt_enum(
            Qt,
            "ClipOperation.IntersectClip",
            "IntersectClip",
            default=qt_enum(QPainter, "ClipOperation.IntersectClip", "IntersectClip")
        )
        painter.setClipRect(
            int(map_x), int(map_y),
            int(map_width), int(map_height),
            clip_operation
        )
        try:
            rotation = float(self.iface.mapCanvas().mapSettings().rotation())
        except (AttributeError, TypeError, ValueError):
            rotation = 0.0
        painter.translate(center_x, ring_center_y)
        painter.rotate(rotation)
        painter.translate(-center_x, -ring_center_y)
        painter.setRenderHint(qt_enum(QPainter, "RenderHint.Antialiasing", "Antialiasing"), True)
        painter.setPen(QPen(QColor("#000000"), max(1, int(0.9 * mm))))
        painter.setBrush(qt_enum(Qt, "BrushStyle.NoBrush", "NoBrush"))
        painter.drawEllipse(
            center_x - outer_radius,
            ring_center_y - outer_radius,
            outer_radius * 2,
            outer_radius * 2
        )
        painter.setPen(QPen(QColor("#000000"), max(1, int(0.5 * mm))))
        painter.drawEllipse(
            center_x - int(5.0 * mm * scale),
            ring_center_y - int(5.0 * mm * scale),
            int(10.0 * mm * scale),
            int(10.0 * mm * scale)
        )

        arrow = QPainterPath()
        arrow.moveTo(center_x, top_y)
        arrow.lineTo(center_x + int(3.7 * mm * scale), int(top_y + 17.0 * mm * scale))
        arrow.lineTo(center_x, int(top_y + 12.5 * mm * scale))
        arrow.lineTo(center_x - int(3.7 * mm * scale), int(top_y + 17.0 * mm * scale))
        arrow.closeSubpath()
        painter.setPen(QPen(QColor("#000000"), max(1, int(1.1 * mm))))
        painter.setBrush(QColor("#1188CC"))
        painter.drawPath(arrow)

        painter.setPen(QColor("#1188CC"))
        font = QFont("Arial")
        font.setPointSizeF(14)
        painter.setFont(font)
        painter.drawText(
            QRect(center_x - int(4.0 * mm * scale), int(top_y - 6.0 * mm * scale),
                  int(8.0 * mm * scale), int(6.0 * mm * scale)),
            qt_enum(Qt, "AlignmentFlag.AlignCenter", "AlignCenter"),
            "N"
        )
        painter.restore()

    def draw_scale_elements(self, painter, map_x, map_bottom, map_width, dpi):
        # 地図の縮尺に合う長さのスケールバーと「S=1/...」を描く。
        map_x = int(map_x)
        map_bottom = int(map_bottom)
        map_width = int(map_width)
        scale = self.scale_denominator()
        painter.setPen(qt_enum(Qt, "GlobalColor.black", "black"))
        font = QFont(self.dock.font_combo.currentText())
        font.setPointSize(10)
        painter.setFont(font)
        base_y = int(map_bottom - 10.0 * dpi / 72.0 + 8.0 * dpi / 25.4)

        target_mm = 50.0
        ground_m = target_mm * scale / 1000.0
        choices = [1, 2, 5]
        magnitude = 10 ** math.floor(math.log10(ground_m))
        normalized = ground_m / magnitude
        nice = min(choices, key=lambda value: abs(normalized - value))
        ground_m = nice * magnitude
        line_mm = ground_m * 1000.0 / scale
        line_px = line_mm / 25.4 * dpi
        x = int(map_x + 4.0 * dpi / 25.4)
        y = int(base_y + 4.0 * dpi / 25.4)
        pen = QPen(
            qt_enum(Qt, "GlobalColor.black", "black"),
            0.3 * dpi / 25.4,
            qt_enum(Qt, "PenStyle.SolidLine", "SolidLine")
        )
        pen.setCosmetic(False)
        painter.setPen(pen)
        painter.drawLine(int(x), int(y), int(x + line_px), int(y))
        painter.drawLine(int(x), int(y - 2 * dpi / 25.4), int(x), int(y + 2 * dpi / 25.4))
        painter.drawLine(int(x + line_px), int(y - 2 * dpi / 25.4),
                         int(x + line_px), int(y + 2 * dpi / 25.4))
        unit = "m" if ground_m < 1000 else "km"
        label_value = ground_m if ground_m < 1000 else ground_m / 1000
        painter.drawText(
            QRect(int(x), int(y + 1.5 * dpi / 25.4), int(line_px), int(7 * dpi / 25.4)),
            qt_enum(Qt, "AlignmentFlag.AlignHCenter", "AlignHCenter") |
            qt_enum(Qt, "AlignmentFlag.AlignTop", "AlignTop"),
            "{} {}".format(int(label_value), unit)
        )
        scale_text = "S=1/{:,.0f}".format(scale)
        painter.setPen(qt_enum(Qt, "GlobalColor.black", "black"))
        painter.drawText(
            QRect(int(x + line_px + 2.0 * dpi / 25.4), int(y - 2.0 * dpi / 25.4),
                  int(45.0 * dpi / 25.4), int(8.0 * dpi / 25.4)),
            qt_enum(Qt, "AlignmentFlag.AlignLeft", "AlignLeft") |
            qt_enum(Qt, "AlignmentFlag.AlignVCenter", "AlignVCenter"),
            scale_text
        )

    def draw_project_copyright(self, painter, map_x, map_bottom, map_width, dpi):
        # 著作権ラベルが設定されている場合だけ、地図下部右側へ描画する。
        copyright_text = self.dock.copyright_edit.text().strip()

        if not copyright_text:
            return

        font = QFont(self.dock.font_combo.currentText())
        font.setPointSize(10)
        font.setLetterSpacing(
            qt_enum(QFont, "SpacingType.AbsoluteSpacing", "AbsoluteSpacing"),
            0.5 * dpi / 72.0
        )
        painter.setFont(font)
        painter.setPen(qt_enum(Qt, "GlobalColor.black", "black"))
        y = int(map_bottom)
        painter.drawText(
            QRect(
                int(map_x),
                y,
                int(map_width),
                int(8.0 * dpi / 25.4)
            ),
            qt_enum(Qt, "AlignmentFlag.AlignRight", "AlignRight") |
            qt_enum(Qt, "AlignmentFlag.AlignTop", "AlignTop"),
            copyright_text
        )
