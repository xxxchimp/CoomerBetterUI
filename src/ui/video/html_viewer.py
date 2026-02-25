from __future__ import annotations

import re

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.ui.common.theme import Colors, Fonts, Spacing
from src.ui.images.async_image_widgets import AsyncImageLabel
from src.ui.images.image_utils import create_circular_pixmap, scale_and_crop_pixmap


class HtmlViewer(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("postContentView")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._apply_tweet_style()

    def set_post_html(self, html: str, allow_external_media: bool) -> None:
        safe_html = html or ""
        if not allow_external_media:
            safe_html = self._strip_external_media(safe_html)
        self.setHtml(safe_html)

    @staticmethod
    def _strip_external_media(html: str) -> str:
        html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<source[^>]*>", "", html, flags=re.IGNORECASE)
        html = re.sub(
            r"<(video|audio|iframe)[^>]*>.*?</\\1>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = re.sub(r"<(video|audio|iframe)[^>]*/>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<object[^>]*>.*?</object>", "", html, flags=re.IGNORECASE | re.DOTALL)
        return html

    @staticmethod
    def _open_link(url: QUrl) -> None:
        if url.isValid():
            QDesktopServices.openUrl(url)

    def _apply_tweet_style(self) -> None:
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setViewportMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            f"""
            QTextBrowser#postContentView {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_XXL}px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QTextBrowser#postContentView:focus {{
                border: 1px solid {Colors.ACCENT_PRIMARY};
            }}
            """
        )
        self.document().setDefaultStyleSheet(
            f"""
            body {{
                margin: 0;
                padding: 0;
                font-family: {Fonts.FAMILY};
                font-size: {Fonts.SIZE_LG}px;
                line-height: 1.4;
                color: {Colors.TEXT_PRIMARY};
            }}
            a {{
                color: {Colors.ACCENT_SECONDARY};
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            p {{
                margin: 0 0 {Spacing.SM}px 0;
            }}
            p:last-child {{
                margin-bottom: 0;
            }}
            blockquote {{
                margin: {Spacing.SM}px 0 {Spacing.SM}px {Spacing.MD}px;
                padding-left: {Spacing.MD}px;
                border-left: 3px solid {Colors.BORDER_LIGHT};
                color: {Colors.TEXT_SECONDARY};
            }}
            code {{
                font-family: "Consolas", "Menlo", monospace;
                background: {Colors.BG_INPUT};
                padding: 1px 4px;
                border-radius: {Spacing.RADIUS_SM}px;
            }}
            pre {{
                background: {Colors.BG_INPUT};
                padding: {Spacing.SM}px;
                border-radius: {Spacing.RADIUS_SM}px;
                white-space: pre-wrap;
            }}
            hr {{
                border: none;
                border-top: 1px solid {Colors.BORDER_DEFAULT};
                margin: {Spacing.MD}px 0;
            }}
            h2.post-title, h3.post-title {{
                margin: 0 0 {Spacing.MD}px 0;
                font-size: {Fonts.SIZE_XXL}px;
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
                color: {Colors.TEXT_PRIMARY};
            }}
            """
        )


class _PopupTitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset = None
        self.setObjectName("htmlPopupTitleBar")
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.MD, Spacing.SM, Spacing.SM, Spacing.SM)
        layout.setSpacing(Spacing.SM)

        self.title_label = QLabel("Post Content")
        self.title_label.setObjectName("htmlPopupTitleLabel")
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.min_btn = QPushButton("-")
        self.min_btn.setObjectName("htmlPopupMinButton")
        self.min_btn.setFixedSize(26, 22)
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("X")
        self.close_btn.setObjectName("htmlPopupCloseButton")
        self.close_btn.setFixedSize(26, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.close_btn)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text or "Post Content")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class HtmlPopupWindow(QWidget):
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setObjectName("htmlPopupWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(420, 240)
        self.resize(720, 520)
        self._banner_height = 140
        self._avatar_size = 48
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("htmlPopupContainer")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self.title_bar = _PopupTitleBar(self._container)
        container_layout.addWidget(self.title_bar)

        self.profile = QFrame(self._container)
        self.profile.setObjectName("htmlPopupProfile")
        profile_layout = QVBoxLayout(self.profile)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(0)

        self.profile_banner = AsyncImageLabel(self.profile)
        self.profile_banner.setObjectName("htmlPopupProfileBanner")
        self.profile_banner.setFixedHeight(self._banner_height)
        self.profile_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.profile_banner.setStyleSheet(f"background-color: {Colors.BG_TERTIARY};")
        profile_layout.addWidget(self.profile_banner)

        profile_content = QWidget(self.profile)
        profile_content_layout = QVBoxLayout(profile_content)
        profile_content_layout.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        profile_content_layout.setSpacing(4)

        header_row = QWidget(profile_content)
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(Spacing.MD)

        self.profile_avatar = AsyncImageLabel(header_row)
        self.profile_avatar.setObjectName("htmlPopupProfileAvatar")
        self.profile_avatar.setFixedSize(self._avatar_size, self._avatar_size)
        self.profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.profile_avatar.setStyleSheet(
            f"""
            QLabel#htmlPopupProfileAvatar {{
                background-color: {Colors.BG_TERTIARY};
                border-radius: {self._avatar_size // 2}px;
            }}
            """
        )
        header_layout.addWidget(self.profile_avatar)

        text_col = QWidget(header_row)
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.profile_name = QLabel("Creator")
        self.profile_name.setObjectName("htmlPopupProfileName")
        self.profile_name.setWordWrap(False)
        text_layout.addWidget(self.profile_name)

        self.profile_meta = QLabel("")
        self.profile_meta.setObjectName("htmlPopupProfileMeta")
        self.profile_meta.setWordWrap(False)
        text_layout.addWidget(self.profile_meta)

        header_layout.addWidget(text_col, 1)
        profile_content_layout.addWidget(header_row)

        self.profile_title = QLabel("")
        self.profile_title.setObjectName("htmlPopupProfileTitle")
        self.profile_title.setWordWrap(True)
        profile_content_layout.addWidget(self.profile_title)

        profile_layout.addWidget(profile_content)
        container_layout.addWidget(self.profile)

        self.viewer = HtmlViewer(self._container)
        container_layout.addWidget(self.viewer, 1)

        layout.addWidget(self._container, 1)

        self.title_bar.min_btn.clicked.connect(self.showMinimized)
        self.title_bar.close_btn.clicked.connect(self.close)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#htmlPopupContainer {{
                background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_XL}px;
            }}
            QWidget#htmlPopupTitleBar {{
                background-color: {Colors.BG_HOVER};
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
                border-top-left-radius: {Spacing.RADIUS_XL}px;
                border-top-right-radius: {Spacing.RADIUS_XL}px;
            }}
            QLabel#htmlPopupTitleLabel {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
                font-weight: {Fonts.WEIGHT_MEDIUM};
            }}
            QPushButton#htmlPopupMinButton,
            QPushButton#htmlPopupCloseButton {{
                background-color: {Colors.BG_TERTIARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Spacing.RADIUS_SM}px;
                color: {Colors.TEXT_PRIMARY};
                padding: 0px;
            }}
            QPushButton#htmlPopupMinButton:hover {{
                border-color: {Colors.ACCENT_SECONDARY};
                background-color: {Colors.BG_HOVER};
            }}
            QPushButton#htmlPopupCloseButton:hover {{
                border-color: {Colors.ACCENT_ERROR};
                background-color: {Colors.ACCENT_ERROR};
                color: {Colors.TEXT_WHITE};
            }}
            QFrame#htmlPopupProfile {{
                background-color: {Colors.BG_TERTIARY};
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
            }}
            QLabel#htmlPopupProfileBanner {{
                border-bottom: 1px solid {Colors.BORDER_DEFAULT};
            }}
            QLabel#htmlPopupProfileName {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_LG}px;
                font-weight: {Fonts.WEIGHT_SEMIBOLD};
            }}
            QLabel#htmlPopupProfileMeta {{
                color: {Colors.TEXT_SECONDARY};
                font-size: {Fonts.SIZE_SM}px;
            }}
            QLabel#htmlPopupProfileTitle {{
                color: {Colors.TEXT_PRIMARY};
                font-size: {Fonts.SIZE_MD}px;
            }}
            QTextBrowser#postContentView {{
                background-color: transparent;
                border: none;
            }}
            """
        )

    def set_title(self, title: str | None) -> None:
        self.title_bar.set_title(title or "Post Content")
        self.setWindowTitle(title or "Post Content")

    def set_profile(
        self,
        creator_name: str | None,
        service: str | None,
        creator_id: str | None,
        title: str | None,
    ) -> None:
        name = creator_name or "Creator"
        service = (service or "").strip()
        creator_id = (creator_id or "").strip()
        meta_parts = []
        if service:
            meta_parts.append(service)
        if creator_id and creator_id != name:
            meta_parts.append(creator_id)
        meta = " • ".join(meta_parts)
        self.profile_name.setText(name)
        self.profile_meta.setText(meta)
        if title:
            self.profile_title.setText(title)
            self.profile_title.setVisible(True)
        else:
            self.profile_title.setText("")
            self.profile_title.setVisible(False)

    def set_profile_images(self, banner_url: str | None, avatar_url: str | None) -> None:
        if banner_url:
            def _on_banner_loaded(_, pixmap: QPixmap) -> None:
                if pixmap.isNull():
                    return
                target_w = max(1, self.profile_banner.width() or self.width())
                target = (target_w, self._banner_height)
                self.profile_banner.setPixmap(scale_and_crop_pixmap(pixmap, target))

            self.profile_banner.load_image(
                url=banner_url,
                target_size=(max(1, self.width()), self._banner_height),
                on_loaded=_on_banner_loaded,
            )
        else:
            self.profile_banner.clear()

        if avatar_url:
            def _on_avatar_loaded(_, pixmap: QPixmap) -> None:
                if pixmap.isNull():
                    return
                scaled = scale_and_crop_pixmap(pixmap, (self._avatar_size, self._avatar_size))
                self.profile_avatar.setPixmap(create_circular_pixmap(scaled, self._avatar_size))

            self.profile_avatar.load_image(
                url=avatar_url,
                target_size=(self._avatar_size, self._avatar_size),
                on_loaded=_on_avatar_loaded,
            )
        else:
            self.profile_avatar.clear()

    def set_html(self, html: str, allow_external_media: bool) -> None:
        self.viewer.set_post_html(html or "", allow_external_media)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
