#!/usr/bin/env python3
"""Gran PDF -> EPUB 3 refluível.

Pipeline-base para PDFs digitais do Gran.

Objetivos:
- preservar o conteúdo intelectual;
- remover resíduos de A4;
- deduplicar spans sobrepostos (ex.: títulos desenhados duas vezes);
- reconstruir parágrafos, listas, questões e legislação;
- extrair/recortar imagens em fundo branco;
- reconstruir tabelas textuais quando PyMuPDF consegue detectá-las;
- preservar cores cromáticas de texto por uma camada pós-estrutura e paleta sRGB canônica;
- preservar negritos reais do PDF por uma camada inline independente da estrutura;
- preservar itálicos somente quando a fonte incorporada fornece evidência visual forte;
- não reconstruir sublinhados desenhados como linhas ou outras figuras geométricas;
- não inferir marca-texto/fundos gráficos por geometria ou pixels;
- produzir XHTML/CSS/EPUB 3 sem fontes incorporadas.

Este conversor é deliberadamente auditável. PDFs diferentes podem exigir um arquivo
de overrides ou pequenos ajustes heurísticos. A conversão não deve ser tratada como
uma operação cega de "pdf2epub".
"""
from __future__ import annotations

import argparse
import collections
import colorsys
import dataclasses
import html
import json
import locale
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:
    fitz = None  # type: ignore[assignment]
try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None  # type: ignore[assignment]


VERSION = "1.4.7"
EPUBCHECK_VERSION = "5.3.0"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"


def require_conversion_dependencies():
    missing = []
    if fitz is None:
        missing.append("PyMuPDF")
    if Image is None:
        missing.append("Pillow")
    if missing:
        raise RuntimeError(
            "Dependências ausentes para converter/inspecionar PDF: "
            + ", ".join(missing)
            + ". Instale requirements.txt. O comando validate continua disponível sem elas."
        )

DEFAULT_DROP_PATTERNS = [
    r"^gran\.com\.br$",
    r"^\d+\s+de\s+\d+$",
    r"^o conteúdo deste livro eletrônico é licenciado para\b.*$",
    r"^vedada,? por quaisquer meios.*$",
    r"^a sua reprodução, cópia, divulgação ou distribuição.*$",
]

QUESTION_RE = re.compile(r"^(?P<num>\d{3})\.\s*(?P<rest>.+)")
ALT_RE = re.compile(r"^(?P<mark>[a-eA-E])\)\s*(?P<rest>.+)")
ROMAN_RE = re.compile(r"^(?P<mark>[IVXLCDM]{1,7})\s*[–—-]\s*(?P<rest>.+)")
ARTICLE_RE = re.compile(r"^(Art\.\s*\d+[ºo°]?\.?)(?:\s+|$)", re.I)
PARAGRAPH_RE = re.compile(r"^(§\s*\d+[ºo°]?|Parágrafo\s+único)\b", re.I)
BULLET_RE = re.compile(r"^(?P<mark>[•◦▪■□◆◇‣⁃])\s*(?P<rest>.*)")
DASH_ITEM_RE = re.compile(r"^(?P<mark>[−–—-])\s+(?P<rest>.+)")
ANSWER_RE = re.compile(r"^(?:Letra\s+[a-eA-E]|Certo|Errado)\.?$", re.I)
CALL_LABEL_RE = re.compile(r"^(DICA|EXEMPLO|OBS\.?|ATENÇÃO|IMPORTANTE)\s*:?$", re.I)
TRAILING_PHYSICAL_HYPHEN_RE = re.compile(r"(?<=[A-Za-zÀ-ÖØ-öø-ÿ])\s*-\s*$")
SPACED_WORD_FRAGMENT_RE = re.compile(
    r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])([A-Za-zÀ-ÖØ-öø-ÿ]{2,})\s+-\s+"
    r"([a-zà-öø-ÿ]{2,})(?![A-Za-zÀ-ÖØ-öø-ÿ])"
)
CALL_INLINE_RE = re.compile(r"^(?P<label>DICA|EXEMPLO|OBS\.?|ATENÇÃO|IMPORTANTE)\s*:\s*(?P<rest>.+)$", re.I)


@dataclasses.dataclass
class Config:
    body_indent_em: float = 1.5
    body_line_height: float = 1.5
    render_scale: float = 2.5
    repeat_ratio: float = 0.30
    header_ratio: float = 0.13
    footer_ratio: float = 0.13
    image_min_area_ratio: float = 0.008
    title_color: str = "#061b5c"
    drop_patterns: list[str] = dataclasses.field(default_factory=lambda: list(DEFAULT_DROP_PATTERNS))
    keep_page_markers: bool = True
    convert_tables: bool = True
    crop_images: bool = True
    # Cores são uma camada visual pós-estrutura. Nunca participam da segmentação.
    preserve_text_colors: bool = True
    text_color_min_saturation: float = 0.30
    text_color_min_channel_spread: int = 24
    text_color_min_value: int = 48


@dataclasses.dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    font: str
    flags: int
    color: int

    @property
    def bold(self) -> bool:
        f = self.font.lower()
        return "bold" in f or bool(self.flags & 16)


@dataclasses.dataclass
class ColorRange:
    start: int
    end: int
    css_class: str


@dataclasses.dataclass
class BoldRange:
    start: int
    end: int


@dataclasses.dataclass
class BoldRun:
    page: int
    text: str
    bbox: tuple[float, float, float, float]


@dataclasses.dataclass
class ItalicRange:
    start: int
    end: int


@dataclasses.dataclass
class ItalicRun:
    page: int
    text: str
    bbox: tuple[float, float, float, float]


@dataclasses.dataclass
class Line:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold_ratio: float
    font: str
    color: int
    color_ranges: list[ColorRange] = dataclasses.field(default_factory=list)
    bold_ranges: list[BoldRange] = dataclasses.field(default_factory=list)
    italic_ranges: list[ItalicRange] = dataclasses.field(default_factory=list)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]


@dataclasses.dataclass
class ImageRegion:
    page: int
    bbox: tuple[float, float, float, float]
    filename: str = ""
    caption: str = ""

    @property
    def y0(self) -> float:
        return self.bbox[1]


@dataclasses.dataclass
class TableRegion:
    page: int
    bbox: tuple[float, float, float, float]
    rows: list[list[Optional[str]]]

    @property
    def y0(self) -> float:
        return self.bbox[1]


@dataclasses.dataclass
class Element:
    kind: str
    page: int
    text: str = ""
    level: int = 0
    marker: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None
    image: Optional[ImageRegion] = None
    table: Optional[TableRegion] = None
    children: list["Element"] = dataclasses.field(default_factory=list)
    color_ranges: list[ColorRange] = dataclasses.field(default_factory=list)
    bold_ranges: list[BoldRange] = dataclasses.field(default_factory=list)
    italic_ranges: list[ItalicRange] = dataclasses.field(default_factory=list)
    # Marcadores de página que caem dentro de uma unidade semântica refluível.
    # Cada tupla contém (offset no texto reconstruído, número da página PDF).
    page_breaks: list[tuple[int, int]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Audit:
    duplicate_spans_removed: int = 0
    repeated_furniture_removed: int = 0
    regex_furniture_removed: int = 0
    images_rendered: int = 0
    tables_detected: int = 0
    table_cells_repaired: int = 0
    semantic_continuations_merged: int = 0
    cross_page_continuations_merged: int = 0
    list_markers: dict[str, int] = dataclasses.field(default_factory=dict)
    chromatic_spans_seen: int = 0
    chromatic_spans_preserved: int = 0
    chromatic_spans_discarded: int = 0
    color_classes: dict[str, int] = dataclasses.field(default_factory=dict)
    bold_fonts_detected: list[str] = dataclasses.field(default_factory=list)
    bold_runs_seen: int = 0
    bold_runs_preserved: int = 0
    bold_runs_discarded: int = 0
    italic_fonts_detected: list[str] = dataclasses.field(default_factory=list)
    italic_runs_seen: int = 0
    italic_runs_preserved: int = 0
    italic_runs_discarded: int = 0
    question_box_line_exclusions_applied: int = 0
    suspicious_lines: list[dict] = dataclasses.field(default_factory=list)
    semantic_warnings: list[dict] = dataclasses.field(default_factory=list)
    archive_validation: dict[str, object] = dataclasses.field(default_factory=dict)
    epubcheck: dict[str, object] = dataclasses.field(default_factory=dict)
    warnings: list[str] = dataclasses.field(default_factory=list)


# --------------------------- text normalization ---------------------------

def ws(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = text.replace("\ufffe", "").replace("\uffff", "")
    text = text.replace("\xad", "")
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text.strip()


def norm_key(text: str) -> str:
    text = ws(text).casefold()
    text = re.sub(r"\d+\s+de\s+\d+", "<page>", text)
    return text


def bbox_key(bbox: Iterable[float], precision: float = 0.5) -> tuple[int, int, int, int]:
    return tuple(int(round(float(v) / precision)) for v in bbox)  # type: ignore[return-value]


def rect_intersection_ratio(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = max(1e-9, (ax1 - ax0) * (ay1 - ay0))
    return inter / area


def join_lines(parts: list[str]) -> str:
    """Une linhas físicas sem transformar toda quebra do PDF em parágrafo.

    Remove hífen apenas quando há forte sinal de hifenização de fim de linha.
    """
    out = ""
    for raw in parts:
        s = ws(raw)
        if not s:
            continue
        if not out:
            out = s
            continue
        # Hifenização de fim de linha: palavra- (ou palavra - na extração) + minúscula.
        # A correção fica restrita à fronteira física entre duas linhas; não há
        # substituição global de hífens que possam ser legítimos no texto-fonte.
        physical_hyphen = TRAILING_PHYSICAL_HYPHEN_RE.search(out)
        if physical_hyphen and re.match(r"^[a-zà-öø-ÿ]", s):
            out = out[:physical_hyphen.start()] + s
        else:
            out += " " + s
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def join_page_fragments(left: str, right: str) -> tuple[str, int, int]:
    """Une texto separado apenas pela paginação e devolve o offset da fronteira.

    O terceiro valor é o limite preservado do fragmento esquerdo. Ele difere do
    offset somente quando um hífen editorial de fim de página é removido.
    """
    left = ws(left)
    right = ws(right)
    physical_hyphen = TRAILING_PHYSICAL_HYPHEN_RE.search(left)
    if physical_hyphen and re.match(r"^[a-zà-öø-ÿ]", right):
        boundary = physical_hyphen.start()
        return left[:boundary] + right, boundary, boundary
    boundary = len(left) + 1
    return left + " " + right, boundary, len(left)


def merge_offset_ranges(left: list, right: list, left_limit: int, right_offset: int) -> list:
    """Combina ranges inline quando a união textual tem offsets determinísticos."""
    merged = []
    for item in left:
        start = min(item.start, left_limit)
        end = min(item.end, left_limit)
        if end > start:
            merged.append(dataclasses.replace(item, start=start, end=end))
    for item in right:
        merged.append(dataclasses.replace(
            item, start=item.start + right_offset, end=item.end + right_offset,
        ))
    return merged


def suspicious_duplicate_word(text: str) -> bool:
    # Detecta duplicação integral e também prefixos longos repetidos por overlays.
    compact = re.sub(r"\s+", " ", text.strip())
    toks = compact.split()
    if len(toks) >= 2 and len(toks) <= 80:
        half = len(toks) // 2
        if len(toks) % 2 == 0 and toks[:half] == toks[half:]:
            return True
        # Ex.: "Mas e quanto ... ao Mas e quanto ... ao governo federal?"
        for n in range(min(16, len(toks)//2), 3, -1):
            if toks[:n] == toks[n:2*n]:
                return True
    return bool(re.search(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{5,})\s+\1\b", compact))


def near_white(color: int) -> bool:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return min(r, g, b) >= 245


def _rgb_from_pdf_color(color: int) -> tuple[int, int, int]:
    """PyMuPDF expõe a cor textual como inteiro sRGB 0xRRGGBB."""
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def classify_text_color(color: int, config: Config) -> Optional[str]:
    """Reduz cores cromáticas do PDF a uma paleta pequena e estável.

    Preto, branco, cinzas, quase-neutros e tons escuros ambíguos são deliberadamente
    ignorados. A cor nunca é usada para decidir estrutura; no pior caso ela é perdida.
    """
    if not config.preserve_text_colors:
        return None
    r, g, b = _rgb_from_pdf_color(color)
    hi, lo = max(r, g, b), min(r, g, b)
    if hi < config.text_color_min_value:
        return None
    if hi - lo < config.text_color_min_channel_spread:
        return None
    h, sat, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if sat < config.text_color_min_saturation:
        return None
    deg = (h * 360.0) % 360.0
    # Amarelos são normalizados para laranja: mantém a distinção cromática com
    # contraste melhor em fundo claro. Ciano é absorvido por azul.
    if deg < 20 or deg >= 345:
        return "cor-vermelha"
    if deg < 70:
        return "cor-laranja"
    if deg < 170:
        return "cor-verde"
    if deg < 260:
        return "cor-azul"
    return "cor-roxa"


def build_line_color_ranges(spans: list[Span], config: Config, audit: Audit) -> list[ColorRange]:
    """Mapeia spans cromáticos para offsets da linha sem alterar o texto."""
    ranges: list[ColorRange] = []
    pos = 0
    for i, sp in enumerate(spans):
        if i:
            pos += 1  # a linha é construída com " ".join(sp.text ...)
        start = pos
        end = start + len(sp.text)
        cls = classify_text_color(sp.color, config)
        if cls:
            audit.chromatic_spans_seen += 1
            ranges.append(ColorRange(start, end, cls))
        pos = end
    return ranges


def slice_line(line: Line, start: int, end: Optional[int] = None) -> Line:
    """Recorta texto e metadados cromáticos de uma linha; bbox/estilo permanecem."""
    if end is None:
        end = len(line.text)
    start = max(0, min(start, len(line.text)))
    end = max(start, min(end, len(line.text)))
    ranges: list[ColorRange] = []
    for cr in line.color_ranges:
        a, b = max(start, cr.start), min(end, cr.end)
        if b > a:
            ranges.append(ColorRange(a - start, b - start, cr.css_class))
    bold_ranges = slice_bold_ranges(line.bold_ranges, start, end)
    italic_ranges = slice_italic_ranges(line.italic_ranges, start, end)
    return dataclasses.replace(
        line, text=line.text[start:end], color_ranges=ranges,
        bold_ranges=bold_ranges, italic_ranges=italic_ranges,
    )


def normalize_color_ranges(text: str, ranges: list[ColorRange]) -> list[ColorRange]:
    """Normaliza ranges; em sobreposição ambígua, prefere não colorir a parte conflitante."""
    valid = [r for r in ranges if 0 <= r.start < r.end <= len(text)]
    valid.sort(key=lambda r: (r.start, r.end, r.css_class))
    out: list[ColorRange] = []
    for cr in valid:
        if out and cr.start < out[-1].end:
            # Sobreposição de classes é um sinal de baixa confiança.
            if cr.css_class == out[-1].css_class:
                out[-1].end = max(out[-1].end, cr.end)
            continue
        if out and cr.css_class == out[-1].css_class and text[out[-1].end:cr.start].isspace():
            out[-1].end = cr.end
        else:
            out.append(dataclasses.replace(cr))
    return out


def map_color_ranges_to_joined_text(final_text: str, lines: list[Line], audit: Audit) -> list[ColorRange]:
    """Projeta cores sobre o texto já reconstruído, sem participar da reconstrução.

    O algoritmo é conservador: tenta mapear a linha inteira; se hifenização ou outra
    normalização impedir isso, só mantém um trecho cromático quando ele tem ocorrência
    única no restante do elemento. Qualquer ambiguidade degrada para texto sem cor.
    """
    mapped: list[ColorRange] = []
    cursor = 0
    for line in lines:
        if not line.color_ranges:
            continue
        line_text = line.text
        pos = final_text.find(line_text, cursor)
        if pos >= 0:
            for cr in line.color_ranges:
                mapped.append(ColorRange(pos + cr.start, pos + cr.end, cr.css_class))
                audit.chromatic_spans_preserved += 1
                audit.color_classes[cr.css_class] = audit.color_classes.get(cr.css_class, 0) + 1
            cursor = pos + len(line_text)
            continue
        # Fallback restritivo para linhas alteradas por remoção de hífen físico.
        for cr in line.color_ranges:
            snippet = line_text[cr.start:cr.end]
            if len(snippet.strip()) < 2:
                audit.chromatic_spans_discarded += 1
                continue
            first = final_text.find(snippet, cursor)
            last = final_text.rfind(snippet, cursor)
            if first >= 0 and first == last:
                mapped.append(ColorRange(first, first + len(snippet), cr.css_class))
                audit.chromatic_spans_preserved += 1
                audit.color_classes[cr.css_class] = audit.color_classes.get(cr.css_class, 0) + 1
                cursor = first + len(snippet)
            else:
                audit.chromatic_spans_discarded += 1
    return normalize_color_ranges(final_text, mapped)


def normalize_bold_ranges(text: str, ranges: list[BoldRange]) -> list[BoldRange]:
    """Normaliza ranges de negrito sem alterar o texto."""
    valid = [r for r in ranges if 0 <= r.start < r.end <= len(text)]
    valid.sort(key=lambda r: (r.start, r.end))
    out: list[BoldRange] = []
    for br in valid:
        if out and br.start <= out[-1].end:
            out[-1].end = max(out[-1].end, br.end)
        elif out and text[out[-1].end:br.start].isspace():
            out[-1].end = br.end
        else:
            out.append(dataclasses.replace(br))
    return out


def slice_bold_ranges(ranges: list[BoldRange], start: int, end: int) -> list[BoldRange]:
    out: list[BoldRange] = []
    for br in ranges:
        a, b = max(start, br.start), min(end, br.end)
        if b > a:
            out.append(BoldRange(a - start, b - start))
    return out


def map_bold_ranges_to_joined_text(final_text: str, lines: list[Line], audit: Audit) -> list[BoldRange]:
    """Projeta negritos sobre o texto reconstruído, sem participar da estrutura."""
    mapped: list[BoldRange] = []
    cursor = 0
    for line in lines:
        if not line.bold_ranges:
            continue
        line_text = line.text
        pos = final_text.find(line_text, cursor)
        if pos >= 0:
            for br in line.bold_ranges:
                mapped.append(BoldRange(pos + br.start, pos + br.end))
                audit.bold_runs_preserved += 1
            cursor = pos + len(line_text)
            continue
        for br in line.bold_ranges:
            snippet = line_text[br.start:br.end]
            if not snippet.strip():
                audit.bold_runs_discarded += 1
                continue
            first = final_text.find(snippet, cursor)
            last = final_text.rfind(snippet, cursor)
            if first >= 0 and first == last:
                mapped.append(BoldRange(first, first + len(snippet)))
                audit.bold_runs_preserved += 1
                cursor = first + len(snippet)
            else:
                audit.bold_runs_discarded += 1
    return normalize_bold_ranges(final_text, mapped)


def normalize_italic_ranges(text: str, ranges: list[ItalicRange]) -> list[ItalicRange]:
    """Normaliza ranges de itálico sem alterar o texto."""
    valid = [r for r in ranges if 0 <= r.start < r.end <= len(text)]
    valid.sort(key=lambda r: (r.start, r.end))
    out: list[ItalicRange] = []
    for ir in valid:
        if out and ir.start <= out[-1].end:
            out[-1].end = max(out[-1].end, ir.end)
        elif out and text[out[-1].end:ir.start].isspace():
            out[-1].end = ir.end
        else:
            out.append(dataclasses.replace(ir))
    return out


def slice_italic_ranges(ranges: list[ItalicRange], start: int, end: int) -> list[ItalicRange]:
    out: list[ItalicRange] = []
    for ir in ranges:
        a, b = max(start, ir.start), min(end, ir.end)
        if b > a:
            out.append(ItalicRange(a - start, b - start))
    return out


def map_italic_ranges_to_joined_text(final_text: str, lines: list[Line], audit: Audit) -> list[ItalicRange]:
    """Projeta itálicos sobre o texto reconstruído, sem participar da estrutura."""
    mapped: list[ItalicRange] = []
    cursor = 0
    for line in lines:
        if not line.italic_ranges:
            continue
        line_text = line.text
        pos = final_text.find(line_text, cursor)
        if pos >= 0:
            for ir in line.italic_ranges:
                mapped.append(ItalicRange(pos + ir.start, pos + ir.end))
                audit.italic_runs_preserved += 1
            cursor = pos + len(line_text)
            continue
        for ir in line.italic_ranges:
            snippet = line_text[ir.start:ir.end]
            if not snippet.strip():
                audit.italic_runs_discarded += 1
                continue
            first = final_text.find(snippet, cursor)
            last = final_text.rfind(snippet, cursor)
            if first >= 0 and first == last:
                mapped.append(ItalicRange(first, first + len(snippet)))
                audit.italic_runs_preserved += 1
                cursor = first + len(snippet)
            else:
                audit.italic_runs_discarded += 1
    return normalize_italic_ranges(final_text, mapped)


def _font_ink_density(font_data: bytes, text: str) -> Optional[float]:
    """Estima o peso visual real dos glifos incorporados no PDF."""
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
        probe = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ ]+", " ", text)
        probe = ws(probe)[:120]
        if not probe:
            return None
        font = ImageFont.truetype(BytesIO(font_data), 64)
        bbox = font.getbbox(probe)
        width = max(20, int(font.getlength(probe) + 20))
        height = max(90, int(bbox[3] - bbox[1] + 20))
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        draw.text((10, 10 - bbox[1]), probe, font=font, fill=0)
        hist = image.histogram()
        ink = sum((255 - i) * count for i, count in enumerate(hist)) / 255.0
        advance = max(1.0, float(font.getlength(probe)))
        return ink / advance
    except Exception:
        return None


def detect_bold_font_names(pdf_path: Path, audit: Audit) -> set[str]:
    """Detecta negrito visual mesmo quando o PDF declara FontWeight 400.

    O Gran usa instâncias de fonte variável com nomes/flags idênticos para pesos
    diferentes. A comparação é feita entre os desenhos de glifos incorporados e
    a instância regular dominante; o conteúdo textual não participa da decisão.
    """
    try:
        import pdfplumber
        from pypdf import PdfReader
    except Exception as exc:
        audit.warnings.append(f"Detecção de negrito indisponível ({exc}); mantendo apenas negritos estruturais.")
        return set()

    counts: collections.Counter[str] = collections.Counter()
    samples: dict[str, str] = collections.defaultdict(str)
    try:
        with pdfplumber.open(str(pdf_path)) as pp:
            for page in pp.pages:
                for ch in page.chars:
                    fontname = str(ch.get("fontname", ""))
                    text = str(ch.get("text", ""))
                    if not fontname or not text:
                        continue
                    counts[fontname] += 1
                    if len(samples[fontname]) < 180 and (text.isalnum() or text.isspace() or re.match(r"[À-ÖØ-öø-ÿ]", text)):
                        samples[fontname] += text
    except Exception as exc:
        audit.warnings.append(f"Falha ao ler fontes para negrito ({exc}).")
        return set()

    font_data: dict[str, bytes] = {}
    try:
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            resources = page.get("/Resources", {})
            fonts = resources.get("/Font", {}) if resources else {}
            for _, ref in fonts.items():
                obj = ref.get_object()
                base = str(obj.get("/BaseFont", "")).lstrip("/")
                if not base or base in font_data:
                    continue
                desc = None
                try:
                    if obj.get("/Subtype") == "/Type0":
                        desc = obj["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
                    elif obj.get("/FontDescriptor"):
                        desc = obj["/FontDescriptor"].get_object()
                except Exception:
                    desc = None
                if not desc:
                    continue
                for key in ("/FontFile2", "/FontFile3", "/FontFile"):
                    if key in desc:
                        font_data[base] = desc[key].get_object().get_data()
                        break
    except Exception as exc:
        audit.warnings.append(f"Falha ao ler fontes incorporadas para negrito ({exc}).")

    explicit = {fn for fn in counts if re.search(r"(?:^|[-+_])(x?bold|semibold|demibold)(?:$|[-+_])", fn, re.I)}
    variable = [fn for fn in counts if "MargemVariable" in fn and fn in font_data]
    if not variable:
        audit.bold_fonts_detected = sorted(explicit)
        return explicit

    baseline = max(variable, key=lambda fn: counts[fn])
    base_data = font_data.get(baseline)
    bold = set(explicit)
    if base_data:
        for fn in variable:
            if fn == baseline:
                continue
            sample = samples.get(fn, "")
            own = _font_ink_density(font_data[fn], sample)
            ref = _font_ink_density(base_data, sample)
            if own is not None and ref is not None and ref > 0 and own / ref >= 1.18:
                bold.add(fn)
    audit.bold_fonts_detected = sorted(bold)
    return bold


def _font_slant_score(font_data: bytes, text: str) -> Optional[float]:
    """Mede inclinação visual dos glifos; retorna None quando a amostra é insuficiente."""
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
        preferred = "HINMnilhmuptfkbd0123456789"
        chars: list[str] = []
        for ch in text:
            if ch in preferred and ch not in chars:
                chars.append(ch)
        if len(chars) < 8:
            return None
        font = ImageFont.truetype(BytesIO(font_data), 72)
        scores: list[float] = []
        for ch in chars:
            bbox = font.getbbox(ch)
            width = max(50, bbox[2] - bbox[0] + 40)
            height = max(120, bbox[3] - bbox[1] + 50)
            image = Image.new("L", (width, height), 255)
            ImageDraw.Draw(image).text((20 - bbox[0], 20 - bbox[1]), ch, font=font, fill=0)
            pixels = image.load()
            rows: list[tuple[float, float]] = []
            for y in range(height):
                xs = [x for x in range(width) if pixels[x, y] < 100]
                if len(xs) >= 2:
                    rows.append((float(y), sum(xs) / len(xs)))
            if len(rows) < 8:
                continue
            lo = int(len(rows) * 0.15)
            hi = max(lo + 4, int(len(rows) * 0.85))
            middle = rows[lo:hi]
            mean_y = statistics.mean(y for y, _ in middle)
            mean_x = statistics.mean(x for _, x in middle)
            denominator = sum((y - mean_y) ** 2 for y, _ in middle)
            if denominator <= 0:
                continue
            slope = sum((y - mean_y) * (x - mean_x) for y, x in middle) / denominator
            scores.append(-slope)
        if len(scores) < 8:
            return None
        return float(statistics.median(scores))
    except Exception:
        return None


def detect_italic_font_names(pdf_path: Path, audit: Audit) -> set[str]:
    """Detecta itálico somente por metadado explícito ou inclinação visual inequívoca.

    A medição visual fica restrita às instâncias MargemVariable incorporadas pelo Gran.
    O limiar alto evita transformar pequenas variações de rasterização em itálico.
    """
    try:
        import pdfplumber
        from pypdf import PdfReader
    except Exception as exc:
        audit.warnings.append(f"Detecção de itálico indisponível ({exc}); mantendo texto normal.")
        return set()

    counts: collections.Counter[str] = collections.Counter()
    samples: dict[str, str] = collections.defaultdict(str)
    explicit: set[str] = set()
    try:
        with pdfplumber.open(str(pdf_path)) as pp:
            for page in pp.pages:
                for ch in page.chars:
                    fontname = str(ch.get("fontname", ""))
                    text = str(ch.get("text", ""))
                    if not fontname or not text:
                        continue
                    counts[fontname] += 1
                    if re.search(r"(?:^|[-+_])(italic|oblique)(?:$|[-+_])", fontname, re.I) or ch.get("upright") is False:
                        explicit.add(fontname)
                    if len(samples[fontname]) < 240 and (text.isalnum() or text.isspace()):
                        samples[fontname] += text
    except Exception as exc:
        audit.warnings.append(f"Falha ao ler fontes para itálico ({exc}); mantendo texto normal.")
        return set()

    font_data: dict[str, bytes] = {}
    try:
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            resources = page.get("/Resources", {})
            fonts = resources.get("/Font", {}) if resources else {}
            for _, ref in fonts.items():
                obj = ref.get_object()
                base = str(obj.get("/BaseFont", "")).lstrip("/")
                if not base or base in font_data:
                    continue
                desc = None
                try:
                    if obj.get("/Subtype") == "/Type0":
                        desc = obj["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
                    elif obj.get("/FontDescriptor"):
                        desc = obj["/FontDescriptor"].get_object()
                except Exception:
                    desc = None
                if not desc:
                    continue
                for key in ("/FontFile2", "/FontFile3", "/FontFile"):
                    if key in desc:
                        font_data[base] = desc[key].get_object().get_data()
                        break
    except Exception as exc:
        audit.warnings.append(f"Falha ao ler fontes incorporadas para itálico ({exc}); usando apenas metadados explícitos.")

    italic = set(explicit)
    for fontname in counts:
        if "MargemVariable" not in fontname or fontname not in font_data:
            continue
        score = _font_slant_score(font_data[fontname], samples.get(fontname, ""))
        if score is not None and score >= 0.12:
            italic.add(fontname)
    audit.italic_fonts_detected = sorted(italic)
    if not italic:
        audit.warnings.append("Nenhuma fonte itálica de alta confiança foi detectada; o texto foi mantido normal.")
    return italic


def extract_bold_runs(pdf_path: Path, bold_fonts: set[str], audit: Audit) -> dict[int, list[BoldRun]]:
    """Extrai runs tipográficos em negrito com coordenadas, sem OCR."""
    result: dict[int, list[BoldRun]] = collections.defaultdict(list)
    if not bold_fonts:
        return result
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pp:
            for pno, page in enumerate(pp.pages, 1):
                current: list[dict] = []

                def emit():
                    nonlocal current
                    if not current:
                        return
                    text = ws("".join(str(c.get("text", "")) for c in current))
                    if text:
                        bbox = (
                            min(float(c["x0"]) for c in current),
                            min(float(c["top"]) for c in current),
                            max(float(c["x1"]) for c in current),
                            max(float(c["bottom"]) for c in current),
                        )
                        result[pno].append(BoldRun(pno, text, bbox))
                        audit.bold_runs_seen += 1
                    current = []

                for c in page.chars:
                    fontname = str(c.get("fontname", ""))
                    if fontname not in bold_fonts:
                        emit()
                        continue
                    if current:
                        prev = current[-1]
                        same_line = abs(float(c["top"]) - float(prev["top"])) <= 1.6
                        gap = float(c["x0"]) - float(prev["x1"])
                        max_gap = max(8.0, float(c.get("size", 12.0)) * 0.85)
                        if not same_line or gap > max_gap:
                            emit()
                    current.append(c)
                emit()
    except Exception as exc:
        audit.warnings.append(f"Falha ao extrair runs de negrito ({exc}).")
    return result


def map_bold_runs_to_line(line: Line, runs: list[BoldRun]) -> list[BoldRange]:
    """Mapeia runs geométricos de negrito para offsets da linha extraída."""
    ranges: list[BoldRange] = []
    lx0, ly0, lx1, ly1 = line.bbox
    for run in runs:
        rx0, ry0, rx1, ry1 = run.bbox
        if min(ly1, ry1) - max(ly0, ry0) <= 0:
            continue
        if rx1 < lx0 - 2 or rx0 > lx1 + 2:
            continue
        snippet = ws(run.text)
        if not snippet:
            continue
        starts = [m.start() for m in re.finditer(re.escape(snippet), line.text)]
        if len(starts) == 1:
            start = starts[0]
            ranges.append(BoldRange(start, start + len(snippet)))
    return normalize_bold_ranges(line.text, ranges)


def extract_italic_runs(pdf_path: Path, italic_fonts: set[str], audit: Audit) -> dict[int, list[ItalicRun]]:
    """Extrai runs itálicos de alta confiança com coordenadas, sem OCR."""
    result: dict[int, list[ItalicRun]] = collections.defaultdict(list)
    if not italic_fonts:
        return result
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pp:
            for pno, page in enumerate(pp.pages, 1):
                current: list[dict] = []

                def emit():
                    nonlocal current
                    if not current:
                        return
                    text = ws("".join(str(c.get("text", "")) for c in current))
                    if text:
                        bbox = (
                            min(float(c["x0"]) for c in current),
                            min(float(c["top"]) for c in current),
                            max(float(c["x1"]) for c in current),
                            max(float(c["bottom"]) for c in current),
                        )
                        result[pno].append(ItalicRun(pno, text, bbox))
                        audit.italic_runs_seen += 1
                    current = []

                for c in page.chars:
                    if str(c.get("fontname", "")) not in italic_fonts:
                        emit()
                        continue
                    if current:
                        prev = current[-1]
                        same_line = abs(float(c["top"]) - float(prev["top"])) <= 1.6
                        gap = float(c["x0"]) - float(prev["x1"])
                        max_gap = max(8.0, float(c.get("size", 12.0)) * 0.85)
                        if not same_line or gap > max_gap:
                            emit()
                    current.append(c)
                emit()
    except Exception as exc:
        audit.warnings.append(f"Falha ao extrair runs de itálico ({exc}); mantendo texto normal.")
    return result


def map_italic_runs_to_line(line: Line, runs: list[ItalicRun]) -> list[ItalicRange]:
    """Mapeia runs geométricos de itálico para offsets da linha extraída."""
    ranges: list[ItalicRange] = []
    lx0, ly0, lx1, ly1 = line.bbox
    for run in runs:
        rx0, ry0, rx1, ry1 = run.bbox
        if min(ly1, ry1) - max(ly0, ry0) <= 0:
            continue
        if rx1 < lx0 - 2 or rx0 > lx1 + 2:
            continue
        snippet = ws(run.text)
        if not snippet:
            continue
        starts = [m.start() for m in re.finditer(re.escape(snippet), line.text)]
        if len(starts) == 1:
            start = starts[0]
            ranges.append(ItalicRange(start, start + len(snippet)))
    return normalize_italic_ranges(line.text, ranges)


# --------------------------- PDF analysis ---------------------------

class PDFAnalyzer:
    def __init__(self, pdf_path: Path, config: Config, overrides: dict, audit: Audit):
        self.pdf_path = pdf_path
        self.config = config
        self.overrides = overrides
        self.audit = audit
        self.doc = fitz.open(str(pdf_path))
        self.page_lines: dict[int, list[Line]] = {}
        self.page_images: dict[int, list[ImageRegion]] = collections.defaultdict(list)
        self.page_tables: dict[int, list[TableRegion]] = collections.defaultdict(list)
        self.page_question_boxes: dict[int, list[tuple[float, float, float, float]]] = collections.defaultdict(list)
        self.body_size = 12.0
        self.repeated_keys: set[str] = set()
        self.drop_res = [re.compile(p, re.I) for p in config.drop_patterns + overrides.get("drop_patterns", [])]
        self.bold_font_names = detect_bold_font_names(pdf_path, audit)
        self.page_bold_runs = extract_bold_runs(pdf_path, self.bold_font_names, audit)
        self.italic_font_names = detect_italic_font_names(pdf_path, audit)
        self.page_italic_runs = extract_italic_runs(pdf_path, self.italic_font_names, audit)

    def close(self):
        self.doc.close()

    def _page_raw_lines(self, pno: int) -> list[Line]:
        page = self.doc[pno]
        d = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT)
        out: list[Line] = []
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for l in block.get("lines", []):
                spans: list[Span] = []
                seen = set()
                for s in l.get("spans", []):
                    txt = ws(s.get("text", ""))
                    if not txt:
                        continue
                    key = (bbox_key(s["bbox"]), txt, round(float(s.get("size", 0)), 2), s.get("font", ""))
                    if key in seen:
                        self.audit.duplicate_spans_removed += 1
                        continue
                    seen.add(key)
                    spans.append(Span(txt, tuple(s["bbox"]), float(s.get("size", 0)), s.get("font", ""), int(s.get("flags", 0)), int(s.get("color", 0))))
                if not spans:
                    continue
                spans.sort(key=lambda x: x.bbox[0])
                # Avoid concatenating duplicate overlay spans that survived due tiny bbox shifts.
                clean: list[Span] = []
                for sp in spans:
                    if clean and sp.text == clean[-1].text and rect_intersection_ratio(sp.bbox, clean[-1].bbox) > 0.92:
                        self.audit.duplicate_spans_removed += 1
                        continue
                    clean.append(sp)
                text = ws(" ".join(sp.text for sp in clean))
                if not text:
                    continue
                x0 = min(sp.bbox[0] for sp in clean)
                y0 = min(sp.bbox[1] for sp in clean)
                x1 = max(sp.bbox[2] for sp in clean)
                y1 = max(sp.bbox[3] for sp in clean)
                total_chars = sum(max(1, len(sp.text)) for sp in clean)
                size = sum(sp.size * max(1, len(sp.text)) for sp in clean) / total_chars
                bold_chars = sum(max(1, len(sp.text)) for sp in clean if sp.bold)
                bold_ratio = bold_chars / total_chars
                dominant_font = collections.Counter(sp.font for sp in clean).most_common(1)[0][0]
                dominant_color = collections.Counter(sp.color for sp in clean).most_common(1)[0][0]
                color_ranges = build_line_color_ranges(clean, self.config, self.audit)
                temp_line = Line(pno + 1, text, (x0, y0, x1, y1), size, bold_ratio, dominant_font, dominant_color, color_ranges)
                temp_line.bold_ranges = map_bold_runs_to_line(temp_line, self.page_bold_runs.get(pno + 1, []))
                temp_line.italic_ranges = map_italic_runs_to_line(temp_line, self.page_italic_runs.get(pno + 1, []))
                out.append(temp_line)
        out.sort(key=lambda x: (round(x.y0, 1), x.x0))
        # Alguns PDFs do Gran desenham a mesma linha duas vezes como objetos
        # independentes, não apenas como spans duplicados dentro da mesma linha.
        # Deduplicação de segundo nível evita APRESENTAÇÃO APRESENTAÇÃO e caixas
        # repetidas sem tocar em repetição textual legítima em posições distintas.
        deduped: list[Line] = []
        for ln in out:
            handled = False
            # Overlays do Gran frequentemente criam uma linha branca/invisível e outra
            # cromática no mesmo y. Às vezes a invisível é apenas um prefixo da visível.
            # Elas são duas representações do MESMO texto visual, não duas linhas de leitura.
            for idx in range(len(deduped) - 1, max(-1, len(deduped) - 9), -1):
                prev = deduped[idx]
                if abs(prev.y0 - ln.y0) > 1.5 or abs(prev.y1 - ln.y1) > 1.5:
                    continue
                pw = max(1e-9, prev.bbox[2] - prev.bbox[0]); lw = max(1e-9, ln.bbox[2] - ln.bbox[0])
                ix = max(0.0, min(prev.bbox[2], ln.bbox[2]) - max(prev.bbox[0], ln.bbox[0]))
                overlap_short = ix / min(pw, lw)
                if overlap_short < 0.85 or abs(prev.x0 - ln.x0) > 3.0:
                    continue
                a, b = ws(prev.text), ws(ln.text)
                related = (a == b or a in b or b in a)
                if not related:
                    continue
                # Quando uma camada é quase branca, descarte-a em favor da visível.
                if near_white(prev.color) != near_white(ln.color):
                    keep_ln = near_white(prev.color) and not near_white(ln.color)
                else:
                    # Sem pista cromática, a representação mais completa vence.
                    keep_ln = len(b) > len(a) or (len(b) == len(a) and lw > pw)
                if keep_ln:
                    deduped[idx] = ln
                self.audit.duplicate_spans_removed += 1
                handled = True
                break
            if not handled:
                deduped.append(ln)
        deduped.sort(key=lambda x: (round(x.y0, 1), x.x0))
        return deduped

    def extract_all_lines(self):
        sizes = []
        for pno in range(len(self.doc)):
            lines = self._page_raw_lines(pno)
            self.page_lines[pno + 1] = lines
            for ln in lines:
                if 8 <= ln.size <= 16 and len(ln.text) > 20:
                    sizes.append(ln.size)
        if sizes:
            self.body_size = statistics.median(sizes)

    def detect_repeating_furniture(self):
        counts: collections.Counter[str] = collections.Counter()
        positions: dict[str, list[str]] = collections.defaultdict(list)
        pages_with: dict[str, set[int]] = collections.defaultdict(set)
        for pno, lines in self.page_lines.items():
            page = self.doc[pno - 1]
            h = page.rect.height
            for ln in lines:
                where = None
                if ln.y0 <= h * self.config.header_ratio:
                    where = "header"
                elif ln.y1 >= h * (1 - self.config.footer_ratio):
                    where = "footer"
                if not where:
                    continue
                k = norm_key(ln.text)
                if not k or len(k) > 260:
                    continue
                pages_with[k].add(pno)
                positions[k].append(where)
        n = max(1, len(self.doc) - 2)
        for k, pages in pages_with.items():
            if len(pages) / n >= self.config.repeat_ratio:
                self.repeated_keys.add(k)

    def _is_furniture(self, line: Line) -> bool:
        k = norm_key(line.text)
        if k in self.repeated_keys:
            self.audit.repeated_furniture_removed += 1
            return True
        if any(r.match(k) for r in self.drop_res):
            self.audit.regex_furniture_removed += 1
            return True
        # explicit Gran page counter variations
        if re.fullmatch(r"gran\.com\.br\s+\d+\s+de\s+\d+", k):
            self.audit.regex_furniture_removed += 1
            return True
        return False

    def detect_tables(self):
        if not self.config.convert_tables:
            return
        # find_tables() é relativamente caro. Em vez de executá-lo cegamente em
        # centenas de páginas, usamos sinais conservadores e aceitamos páginas
        # forçadas por override.
        forced = {int(x) for x in self.overrides.get("table_pages", [])}
        table_words = re.compile(r"\b(TABELA|QUADRO|MAPA MENTAL|COMPARATIV[AO]|COMPARAÇÃO)\b", re.I)
        for pno in range(len(self.doc)):
            page_no = pno + 1
            plain = " ".join(ln.text for ln in self.page_lines.get(page_no, []))
            should_scan = page_no in forced or bool(table_words.search(plain))
            if not should_scan:
                continue
            page = self.doc[pno]
            try:
                finder = page.find_tables(vertical_strategy="lines_strict", horizontal_strategy="lines_strict")
            except Exception as exc:
                self.audit.warnings.append(f"Tabela: página {page_no}: {exc}")
                continue
            for t in getattr(finder, "tables", []):
                try:
                    rows = t.extract()
                    if rows and len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                        self.page_tables[page_no].append(TableRegion(page_no, tuple(t.bbox), rows))
                        self.audit.tables_detected += 1
                except Exception:
                    pass

    def repair_repeated_table_cells(self):
        """Preenche célula vazia apenas a partir de outra tabela repetida no mesmo PDF.

        Não infere conteúdo. A tabela doadora precisa ter as mesmas dimensões e todos
        os demais campos não vazios precisam coincidir após normalização.
        """
        tables = [tb for pno in sorted(self.page_tables) for tb in self.page_tables[pno]]
        for target in tables:
            if not any(not ws(cell or "") for row in target.rows for cell in row):
                continue
            dims = (len(target.rows), tuple(len(r) for r in target.rows))
            for donor in tables:
                if donor is target or (len(donor.rows), tuple(len(r) for r in donor.rows)) != dims:
                    continue
                compatible = True
                repairs = []
                for ri, row in enumerate(target.rows):
                    for ci, cell in enumerate(row):
                        a = ws(cell or "").replace("\n", " " )
                        btxt = ws(donor.rows[ri][ci] or "").replace("\n", " " )
                        if a and btxt and a != btxt:
                            compatible = False
                            break
                        if not a and btxt:
                            repairs.append((ri, ci, donor.rows[ri][ci]))
                    if not compatible:
                        break
                if compatible and repairs:
                    for ri, ci, value in repairs:
                        target.rows[ri][ci] = value
                        self.audit.table_cells_repaired += 1
                    break

    def audit_table_geometry(self):
        """Warn if a detected table probably lost a left edge column.

        This is conservative: it only flags multiple short, table-like tokens vertically
        inside the table but geometrically just to its left. It never rewrites content.
        """
        token_re = re.compile(r'^(?:[VFC]|[pqrPQR]|[¬~]?\s*[pqrPQR]|[pqrPQR]\s*[∧∨→↔]|[0-9]+)$')
        for pno, tables in self.page_tables.items():
            lines = self.page_lines.get(pno, [])
            for tb in tables:
                suspects = []
                for ln in lines:
                    cy = (ln.bbox[1] + ln.bbox[3]) / 2
                    if not (tb.bbox[1] <= cy <= tb.bbox[3]):
                        continue
                    if ln.bbox[2] >= tb.bbox[0] - 4:
                        continue
                    txt = ws(ln.text)
                    if len(txt) <= 24 and token_re.match(txt):
                        suspects.append(txt)
                if len(suspects) >= 2:
                    self.audit.semantic_warnings.append({
                        "page": pno, "type": "possible-missing-table-edge-column",
                        "table_bbox": [round(x, 2) for x in tb.bbox], "tokens": suspects[:8],
                    })

    def detect_question_boxes(self):
        """Detecta caixas editoriais de pergunta desenhadas como contorno azul arredondado.

        A geometria serve apenas para identificar a caixa; o conteúdo continua vindo da
        camada textual selecionável. Exigir curvas no traçado evita confundir tabelas e
        linhas decorativas com caixas de texto.
        """
        self.page_question_boxes.clear()
        for pno, page in enumerate(self.doc, start=1):
            seen: set[tuple[int, int, int, int]] = set()
            for drawing in page.get_drawings():
                rect = drawing.get("rect")
                color = drawing.get("color")
                width = float(drawing.get("width") or 0.0)
                items = drawing.get("items") or []
                if rect is None or color is None:
                    continue
                if not any(item and item[0] == "c" for item in items):
                    continue
                if not (120.0 <= rect.width <= page.rect.width * 0.95 and 18.0 <= rect.height <= 110.0):
                    continue
                if not (0.4 <= width <= 2.0):
                    continue
                r, g, b = (float(color[0]), float(color[1]), float(color[2]))
                if not (b > r + 0.08 and b > g + 0.08 and b > 0.12):
                    continue
                box = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                # Só aceite traçados que efetivamente contenham texto do fluxo.
                has_text = False
                for ln in self.page_lines.get(pno, []):
                    cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
                    cy = (ln.bbox[1] + ln.bbox[3]) / 2.0
                    if box[0] - 2 <= cx <= box[2] + 2 and box[1] - 2 <= cy <= box[3] + 2:
                        has_text = True
                        break
                if not has_text:
                    continue
                key = bbox_key(box, precision=1.0)
                if key in seen:
                    continue
                seen.add(key)
                self.page_question_boxes[pno].append(box)

    def question_box_for_line(self, pno: int, bbox: tuple[float, float, float, float], text: str = ""):
        for rule in self.overrides.get("question_box_line_exclusions", []):
            try:
                rule_page = rule.get("page")
                if rule_page is not None and int(rule_page) != pno:
                    continue
                flags = re.I if bool(rule.get("ignore_case", False)) else 0
                if re.search(str(rule.get("pattern", "")), ws(text), flags):
                    self.audit.question_box_line_exclusions_applied += 1
                    return None
            except (TypeError, ValueError, re.error) as exc:
                self.audit.warnings.append(f"Exclusão de caixa inválida na página {pno}: {exc}")
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        for box in self.page_question_boxes.get(pno, []):
            if box[0] - 2 <= cx <= box[2] + 2 and box[1] - 2 <= cy <= box[3] + 2:
                return box
        return None

    def detect_images(self):
        for pno in range(len(self.doc)):
            page = self.doc[pno]
            area = page.rect.width * page.rect.height
            d = page.get_text("dict")
            regions = []
            for b in d.get("blocks", []):
                if b.get("type") != 1:
                    continue
                bbox = tuple(b["bbox"])
                barea = max(0, bbox[2]-bbox[0]) * max(0, bbox[3]-bbox[1])
                if barea / area < self.config.image_min_area_ratio:
                    continue
                # ignore image contained substantially inside a detected table
                if any(rect_intersection_ratio(bbox, t.bbox) > 0.85 for t in self.page_tables.get(pno+1, [])):
                    continue
                regions.append(ImageRegion(pno + 1, bbox))
            # user-provided image regions, useful for vector diagrams
            for reg in self.overrides.get("image_regions", []):
                if int(reg.get("page", -1)) == pno + 1:
                    regions.append(ImageRegion(pno + 1, tuple(float(x) for x in reg["bbox"]), caption=reg.get("caption", "")))
            # de-duplicate near-identical regions
            unique = []
            for r in sorted(regions, key=lambda x: (x.bbox[1], x.bbox[0])):
                if any(rect_intersection_ratio(r.bbox, u.bbox) > 0.92 and rect_intersection_ratio(u.bbox, r.bbox) > 0.92 for u in unique):
                    continue
                unique.append(r)
            self.page_images[pno + 1] = unique

    def render_cover(self, out: Path):
        page = self.doc[0]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(out))

    def render_images(self, image_dir: Path):
        image_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for pno, regs in self.page_images.items():
            page = self.doc[pno - 1]
            for idx, reg in enumerate(regs, start=1):
                clip = fitz.Rect(reg.bbox)
                mat = fitz.Matrix(self.config.render_scale, self.config.render_scale)
                pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
                fn = f"p{pno:03d}_img{idx:02d}.png"
                pix.save(str(image_dir / fn))
                # Force true RGB/RGBA on white if reader/library generated indexed oddities.
                try:
                    im = Image.open(image_dir / fn)
                    if im.mode in ("RGBA", "LA"):
                        bg = Image.new("RGB", im.size, "white")
                        alpha = im.getchannel("A") if "A" in im.getbands() else None
                        bg.paste(im.convert("RGB"), mask=alpha)
                        bg.save(image_dir / fn)
                    elif im.mode != "RGB":
                        im.convert("RGB").save(image_dir / fn)
                except Exception:
                    pass
                reg.filename = fn
                count += 1
        self.audit.images_rendered = count

    def text_lines_without_regions(self, pno: int) -> list[Line]:
        lines = []
        regions = [t.bbox for t in self.page_tables.get(pno, [])] + [i.bbox for i in self.page_images.get(pno, [])]
        for ln in self.page_lines[pno]:
            if self._is_furniture(ln):
                continue
            if any(rect_intersection_ratio(ln.bbox, r) > 0.72 for r in regions):
                continue
            lines.append(ln)
        return lines


# --------------------------- semantic reconstruction ---------------------------

class SemanticBuilder:
    def __init__(self, analyzer: PDFAnalyzer, audit: Audit, overrides: dict):
        self.a = analyzer
        self.audit = audit
        self.overrides = overrides
        self.body_size = analyzer.body_size

    def _heading_level(self, ln: Line) -> int:
        t = ln.text.strip()
        if not t or len(t) > 150:
            return 0
        # explicit override by regex
        for item in self.overrides.get("headings", []):
            if int(item.get("page", ln.page)) != ln.page:
                continue
            if re.search(item["pattern"], t, re.I):
                return int(item.get("level", 2))
        ratio = ln.size / max(1.0, self.body_size)
        allcaps = t.upper() == t and bool(re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", t))
        if ratio >= 1.55:
            return 1
        if ratio >= 1.35 or (allcaps and ratio >= 1.18):
            return 2
        if ratio >= 1.18 and (ln.bold_ratio > 0.45 or allcaps):
            return 3
        return 0

    def _page_left(self, lines: list[Line]) -> float:
        xs = [round(ln.x0) for ln in lines if len(ln.text) > 8 and ln.size <= self.body_size * 1.15]
        if not xs:
            return 48.0
        # A moda falha em páginas dominadas por listas, porque o recuo pendente
        # pode virar o x mais frequente. O menor x textual é a melhor estimativa
        # conservadora da margem-base do corpo após a remoção de cabeçalho/rodapé.
        return float(min(xs))

    def _new_paragraph_by_indent(self, prev: Line, cur: Line, base_left: float) -> bool:
        # Gran usa tipicamente ~20 pt de recuo na PRIMEIRA linha do parágrafo.
        # Logo, qualquer nova linha no recuo de primeira linha pode iniciar parágrafo,
        # inclusive quando o parágrafo anterior tinha apenas uma linha e também
        # começava no mesmo x (caso "Olá, querido(a) aluno(a)!").
        if cur.x0 >= base_left + 12:
            return True
        gap = cur.y0 - prev.y1
        line_h = max(10.0, self.body_size * 1.15)
        if gap > line_h * 0.72:
            return True
        return False

    def _kind_start(self, text: str) -> Optional[tuple[str, str, str]]:
        t = ws(text)
        m = QUESTION_RE.match(t)
        if m:
            return ("question", m.group("num") + ".", m.group("rest"))
        m = ALT_RE.match(t)
        if m:
            return ("alternative", m.group("mark").lower() + ")", m.group("rest"))
        m = ROMAN_RE.match(t)
        if m:
            return ("roman", m.group("mark") + " –", m.group("rest"))
        m = BULLET_RE.match(t)
        if m:
            return ("bullet", m.group("mark"), m.group("rest"))
        m = DASH_ITEM_RE.match(t)
        if m:
            return ("dash", m.group("mark"), m.group("rest"))
        if ARTICLE_RE.match(t):
            return ("legal", "", t)
        if PARAGRAPH_RE.match(t):
            return ("legal-item", "", t)
        if ANSWER_RE.match(t):
            return ("answer", "", t)
        m = CALL_INLINE_RE.match(t)
        if m:
            # Preserve a pontuação editorial: OBS. no PDF aparece como "Obs.: texto".
            return ("call-inline", m.group("label").rstrip(":") + ":", m.group("rest"))
        if CALL_LABEL_RE.match(t):
            return ("call-label", "", t.rstrip(":"))
        return None

    def page_elements(self, pno: int) -> list[Element]:
        lines = self.a.text_lines_without_regions(pno)

        # Remove número de questão desenhado duas vezes por overlays cromáticos.
        filtered: list[Line] = []
        for ln in lines:
            t = ws(ln.text)
            if re.fullmatch(r"\d{3}\.", t):
                duplicate = False
                for other in lines:
                    if other is ln:
                        continue
                    ot = ws(other.text)
                    if not ot.startswith(t) or len(ot) <= len(t) + 2:
                        continue
                    same_band = abs(other.y0 - ln.y0) < 2.5 and abs(other.y1 - ln.y1) < 2.5
                    if same_band and (other.bbox[2] - other.bbox[0]) > (ln.bbox[2] - ln.bbox[0]) * 2:
                        duplicate = True
                        break
                if duplicate:
                    self.audit.duplicate_spans_removed += 1
                    continue
            filtered.append(ln)
        lines = filtered

        # Reúne títulos em caixa alta quebrados fisicamente em duas linhas quando
        # tipografia, alinhamento e distância vertical confirmam uma única unidade.
        merged_lines: list[Line] = []
        i = 0
        while i < len(lines):
            cur = lines[i]
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                ct, nt = ws(cur.text), ws(nxt.text)
                caps1 = ct.upper() == ct and bool(re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", ct))
                caps2 = nt.upper() == nt and bool(re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", nt))
                gap = nxt.y0 - cur.y1
                same_style = abs(cur.size - nxt.size) <= 0.35 and abs(cur.x0 - nxt.x0) <= 24
                if caps1 and caps2 and same_style and -1 <= gap <= 4.0 and len(ct + " " + nt) <= 150:
                    bbox = (min(cur.bbox[0], nxt.bbox[0]), min(cur.bbox[1], nxt.bbox[1]),
                            max(cur.bbox[2], nxt.bbox[2]), max(cur.bbox[3], nxt.bbox[3]))
                    # Títulos cromáticos fundidos são raros; não invente mapeamento de cor.
                    cur = dataclasses.replace(cur, text=ct + " " + nt, bbox=bbox,
                                              size=max(cur.size, nxt.size),
                                              bold_ratio=max(cur.bold_ratio, nxt.bold_ratio),
                                              color_ranges=[], bold_ranges=[], italic_ranges=[])
                    i += 1
            merged_lines.append(cur)
            i += 1
        lines = merged_lines
        base_left = self._page_left(lines)
        events: list[tuple[float, str, object]] = []
        for ln in lines:
            events.append((ln.y0, "line", ln))
        for im in self.a.page_images.get(pno, []):
            events.append((im.y0, "image", im))
        for tb in self.a.page_tables.get(pno, []):
            events.append((tb.y0, "table", tb))
        events.sort(key=lambda x: (x[0], 0 if x[1] != "line" else 1))

        elems: list[Element] = []
        current: Optional[Element] = None
        current_lines: list[Line] = []
        current_label: Optional[str] = None

        def flush():
            nonlocal current, current_lines, current_label
            if current is None:
                return
            if current_lines:
                current.text = join_lines([ln.text for ln in current_lines])
                current.color_ranges = map_color_ranges_to_joined_text(current.text, current_lines, self.audit)
                current.bold_ranges = map_bold_ranges_to_joined_text(current.text, current_lines, self.audit)
                current.italic_ranges = map_italic_ranges_to_joined_text(current.text, current_lines, self.audit)
            if current.text:
                if suspicious_duplicate_word(current.text):
                    self.audit.suspicious_lines.append({"page": pno, "text": current.text, "kind": current.kind})
                elems.append(current)
            current = None
            current_lines = []
            current_label = None

        prev_line: Optional[Line] = None
        for _, typ, obj in events:
            if typ == "image":
                flush()
                elems.append(Element("image", pno, image=obj, bbox=obj.bbox))
                prev_line = None
                continue
            if typ == "table":
                flush()
                elems.append(Element("table", pno, table=obj, bbox=obj.bbox))
                prev_line = None
                continue
            ln: Line = obj  # type: ignore[assignment]
            t = ws(ln.text)
            if not t:
                continue

            # Caixas editoriais reais do PDF prevalecem sobre heurísticas de parágrafo/título.
            # Linhas múltiplas dentro do mesmo contorno são reunidas numa única caixa refluível.
            qbox = self.a.question_box_for_line(pno, ln.bbox, t)
            if qbox is not None:
                if current is None or current.kind != "question-callout" or current.bbox != qbox:
                    flush()
                    current = Element("question-callout", pno, text="", bbox=qbox)
                    current_lines = [ln]
                else:
                    current_lines.append(ln)
                prev_line = ln
                continue
            if current is not None and current.kind == "question-callout":
                flush()

            # A numeração canônica de questão prevalece sobre tamanho e negrito.
            # Algumas questões do Gran usam fonte maior e, sem esta prioridade,
            # seriam promovidas indevidamente a título e perderiam o espaçamento.
            starter = self._kind_start(t)
            hlevel = 0 if starter and starter[0] == "question" else self._heading_level(ln)
            if hlevel:
                flush()
                # Títulos/subtítulos mantêm a cor editorial definida no CSS (#061b5c).
                # Cores de spans do PDF não devem sobrescrever h1-h4.
                elems.append(Element("heading", pno, text=t, level=hlevel, bbox=ln.bbox, color_ranges=[], bold_ranges=[], italic_ranges=[]))
                self.audit.chromatic_spans_discarded += len(ln.color_ranges)
                prev_line = ln
                continue
            if starter:
                kind, marker, rest = starter
                if kind == "call-label":
                    flush()
                    current = Element("callout", pno, text="", marker=rest, bbox=ln.bbox)
                    current_lines = []
                    current_label = rest
                    prev_line = ln
                    continue
                if kind == "call-inline":
                    flush()
                    current = Element("callout", pno, text="", marker=marker, bbox=ln.bbox)
                    rest_pos = ln.text.find(rest)
                    fake = slice_line(ln, rest_pos if rest_pos >= 0 else len(ln.text)) if rest_pos >= 0 else dataclasses.replace(ln, text=rest, color_ranges=[], bold_ranges=[], italic_ranges=[])
                    current_lines = [fake]
                    current_label = marker
                    prev_line = ln
                    continue
                flush()
                current = Element(kind, pno, text="", marker=marker, bbox=ln.bbox)
                # Preserve only rest for markers; legal keeps full text.
                if kind in {"question", "alternative", "roman", "bullet", "dash"}:
                    rest_pos = ln.text.find(rest)
                    fake = slice_line(ln, rest_pos if rest_pos >= 0 else len(ln.text)) if rest_pos >= 0 else dataclasses.replace(ln, text=rest, color_ranges=[], bold_ranges=[], italic_ranges=[])
                    current_lines = [fake]
                else:
                    current_lines = [ln]
                prev_line = ln
                continue

            # Short boxed question-like lines: semantic callout candidate.
            if t.endswith("?") and len(t) <= 120 and (ln.bold_ratio > 0.30 or "sans" in ln.font.lower()):
                flush()
                elems.append(Element(
                    "question-callout", pno, text=t, bbox=ln.bbox,
                    color_ranges=ln.color_ranges, bold_ranges=ln.bold_ranges,
                    italic_ranges=ln.italic_ranges,
                ))
                for cr in ln.color_ranges:
                    self.audit.chromatic_spans_preserved += 1
                    self.audit.color_classes[cr.css_class] = self.audit.color_classes.get(cr.css_class, 0) + 1
                prev_line = ln
                continue

            if current is None:
                current = Element("p", pno, bbox=ln.bbox)
                current_lines = [ln]
            elif current.kind == "callout":
                # Caixas Gran podem ter forte recuo interno. Não use o simples aumento
                # de x para encerrar a caixa; isso separava "Obs." do próprio conteúdo.
                gap = (ln.y0 - prev_line.y1) if prev_line else 0.0
                line_h = max(10.0, self.body_size * 1.15)
                clear_outdent = bool(prev_line and len(current_lines) >= 2 and (prev_line.x0 - ln.x0) > 15)
                if prev_line and current_lines and (gap > line_h * 0.65 or clear_outdent):
                    flush()
                    current = Element("p", pno, bbox=ln.bbox)
                    current_lines = [ln]
                else:
                    current_lines.append(ln)
            elif current.kind in {"question", "alternative", "roman", "bullet", "dash", "legal", "legal-item"}:
                # Wrapped lines stay in the same semantic unit, but a substantial
                # vertical gap (or a clear outdent for lists) ends the unit.
                # This prevents page-layout line breaks from fragmenting list text
                # while also preventing the last item from swallowing later prose.
                gap = (ln.y0 - prev_line.y1) if prev_line else 0.0
                line_h = max(10.0, self.body_size * 1.15)
                break_unit = False
                if prev_line and current.kind in {"bullet", "dash"}:
                    start_x = current.bbox[0] if current.bbox else base_left
                    break_unit = ln.x0 < start_x - 7 or gap > line_h * 0.72
                elif prev_line and current.kind in {"legal", "legal-item"}:
                    # Texto legal é contínuo: recuo/linha física nunca basta para quebrá-lo.
                    # Só encerre diante de um gap estrutural claro; novos Art./§/incisos
                    # já são semantic starters e provocam flush antes deste ponto.
                    break_unit = gap > line_h * 1.05
                elif prev_line and current.kind in {"question", "alternative", "roman"}:
                    break_unit = gap > line_h * 0.72
                if break_unit:
                    flush()
                    current = Element("p", pno, bbox=ln.bbox)
                    current_lines = [ln]
                else:
                    current_lines.append(ln)
            else:
                if prev_line and self._new_paragraph_by_indent(prev_line, ln, base_left):
                    flush()
                    current = Element("p", pno, bbox=ln.bbox)
                    current_lines = [ln]
                else:
                    current_lines.append(ln)
            prev_line = ln
        flush()

        # Merge false physical breaks. Lists use a hanging indent, therefore a
        # continuation must not be promoted to <p> merely because its x changed.
        merged: list[Element] = []
        for el in elems:
            if merged and el.kind == "p" and merged[-1].kind in {"p", "alternative", "roman", "question", "legal", "bullet", "dash"}:
                prev = merged[-1]
                nonterminal = bool(prev.text and prev.text[-1] not in ".!?;:")
                lower_cont = bool(el.text and re.match(r"^[a-zà-öø-ÿ]", el.text))
                list_hanging = False
                if prev.kind in {"bullet", "dash"} and prev.bbox and el.bbox:
                    list_hanging = el.bbox[0] >= prev.bbox[0] + 6
                if prev.text and el.text and nonterminal and (lower_cont or list_hanging):
                    prev.text = join_lines([prev.text, el.text])
                    # A união semântica pode remover hífen/alterar offsets. Para garantir
                    # isolamento absoluto entre estrutura e cor, descarte a camada cromática
                    # desse elemento em vez de tentar reajustá-la por inferência.
                    prev.color_ranges = []
                    prev.bold_ranges = []
                    prev.italic_ranges = []
                    self.audit.semantic_continuations_merged += 1
                    continue
            merged.append(el)
        return merged

    def _looks_like_structural_start(self, text: str) -> bool:
        """Evita fundir títulos/subtítulos que a tipografia deixou como p."""
        t = ws(text)
        if re.fullmatch(r"[-–—−•◦]+", t):
            return True
        if re.match(r"^\d+(?:\.\d+)+\.\s+", t):
            return True
        if len(t) <= 80 and t.endswith(":"):
            return True
        return bool(
            len(t) <= 100
            and t.upper() == t
            and re.search(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", t)
        )

    def _is_cross_page_semantic_continuation(
        self,
        prev: Element,
        cur: Element,
        page_elements: dict[int, list[Element]],
    ) -> bool:
        """Reconhece continuações de prosa, listas, questões e caixas entre páginas."""
        supported = {
            "p", "bullet", "dash", "question", "alternative", "roman",
            "legal", "legal-item", "legal-subitem", "callout", "question-callout",
        }
        if prev.kind not in supported or cur.kind != "p" or not prev.text or not cur.text:
            return False
        prev_last_page = prev.page_breaks[-1][1] if prev.page_breaks else prev.page
        if cur.page != prev_last_page + 1 or cur.bbox is None:
            return False

        tail = re.sub(r'["\u201d\u2019)\]]+$', "", prev.text.rstrip())
        if not tail:
            return False
        terminal = tail[-1] in ".!?;:"
        lower_continuation = bool(
            re.match(r'^["\u201c\u2018(\[]*[a-zà-öø-ÿ]', cur.text.lstrip())
        )
        page_lines = self.a.text_lines_without_regions(cur.page)
        base_left = self._page_left(page_lines)
        at_base_margin = cur.bbox[0] <= base_left + 6.0

        # Itens usam recuo pendente: a continuação aparece à direita do marcador.
        if prev.kind in {"bullet", "dash"}:
            return bool(prev.bbox and cur.bbox[0] >= prev.bbox[0] + 6.0)

        if not at_base_margin:
            return False

        if prev.kind == "p":
            if self._looks_like_structural_start(cur.text):
                return False
            if not terminal:
                return True
            # Uma frase pode terminar exatamente no rodapé e o mesmo parágrafo
            # prosseguir com outra frase. Só use a geometria como autoridade quando
            # a página seguinte comprovar que novos parágrafos recebem recuo.
            return any(
                el.kind == "p" and el is not cur and el.bbox
                and el.bbox[0] >= base_left + 12.0
                for el in page_elements.get(cur.page, [])
            )

        if prev.kind == "question":
            if not terminal or tail.endswith(":"):
                return True
            return bool(re.search(r"\bitem\s+subsequente\.?$", tail, re.I))

        if prev.kind in {"alternative", "roman"}:
            return not terminal and lower_continuation

        if prev.kind in {
            "legal", "legal-item", "legal-subitem", "callout", "question-callout",
        }:
            return not terminal

        return False

    def _merge_cross_page_semantics(
        self,
        elements: list[Element],
        page_elements: dict[int, list[Element]],
    ) -> list[Element]:
        merged: list[Element] = []
        for el in elements:
            if merged and self._is_cross_page_semantic_continuation(
                merged[-1], el, page_elements,
            ):
                prev = merged[-1]
                joined, boundary, left_limit = join_page_fragments(prev.text, el.text)
                prev.color_ranges = merge_offset_ranges(
                    prev.color_ranges, el.color_ranges, left_limit, boundary,
                )
                prev.bold_ranges = merge_offset_ranges(
                    prev.bold_ranges, el.bold_ranges, left_limit, boundary,
                )
                prev.italic_ranges = merge_offset_ranges(
                    prev.italic_ranges, el.italic_ranges, left_limit, boundary,
                )
                prev.page_breaks = (
                    [(offset, page) for offset, page in prev.page_breaks if offset <= left_limit]
                    + [(boundary, el.page)]
                    + [(boundary + offset, page) for offset, page in el.page_breaks]
                )
                prev.text = joined
                self.audit.cross_page_continuations_merged += 1
                continue
            merged.append(el)
        return merged

    def all_elements(self) -> list[Element]:
        out = []
        by_page: dict[int, list[Element]] = {}
        skip_pages = set(int(x) for x in self.overrides.get("skip_pages", []))
        for pno in range(1, len(self.a.doc) + 1):
            if pno in skip_pages:
                continue
            if pno == 1:
                # cover handled separately
                continue
            page = self.page_elements(pno)
            by_page[pno] = page
            out.extend(page)
        return self._merge_cross_page_semantics(out, by_page)


def qa_semantic_structure(elements: list[Element], audit: Audit):
    """Conservative structural preflight; warns instead of rewriting content."""
    for el in elements:
        if el.kind in {"bullet", "dash"}:
            marker = el.marker or ("−" if el.kind == "dash" else "•")
            audit.list_markers[marker] = audit.list_markers.get(marker, 0) + 1
            embedded = re.search(r"(?:^|\s)([•−◦▪■□◆◇‣⁃])\s+", el.text)
            if embedded:
                audit.semantic_warnings.append({
                    "page": el.page,
                    "type": "embedded-list-marker",
                    "marker": embedded.group(1),
                    "text": el.text[:220],
                })
    for i, el in enumerate(elements[:-1]):
        nxt = elements[i + 1]
        last_page = el.page_breaks[-1][1] if el.page_breaks else el.page
        cross_page_kinds = {
            "p", "bullet", "dash", "question", "alternative", "roman",
            "legal", "legal-item", "legal-subitem", "callout", "question-callout",
        }
        if (
            el.kind in cross_page_kinds
            and nxt.kind == "p"
            and nxt.page == last_page + 1
            and el.text
            and nxt.text
        ):
            tail = re.sub(r'["\u201d\u2019)\]]+$', "", el.text.rstrip())
            nonterminal = bool(tail and tail[-1] not in ".!?;:")
            lower_continuation = bool(
                re.match(r'^["\u201c\u2018(\[]*[a-zà-öø-ÿ]', nxt.text.lstrip())
            )
            if nonterminal and lower_continuation:
                audit.semantic_warnings.append({
                    "page": nxt.page,
                    "type": "unresolved-cross-page-continuation",
                    "before_kind": el.kind,
                    "before": el.text[-160:],
                    "after": nxt.text[:200],
                })
        if el.kind in {"bullet", "dash"} and nxt.kind == "p" and el.page == nxt.page and el.text and nxt.text:
            nonterminal = el.text[-1] not in ".!?;:"
            continuation = bool(re.match(r'^[a-zà-öø-ÿ“"]', nxt.text))
            if nonterminal and continuation:
                audit.semantic_warnings.append({
                    "page": el.page, "type": "list-fragment",
                    "before": el.text[-120:], "after": nxt.text[:160],
                })
        if el.kind in {"bullet", "dash"} and re.search(r'\bTabela\s+\d+\s*:', el.text, re.I):
            audit.semantic_warnings.append({
                "page": el.page, "type": "caption-swallowed-by-list", "text": el.text[-220:]
            })
        if el.kind == "roman" and re.search(r'\b(?:Assinale|Marque|Supondo que|Nota-se, então|Para que o operador)\b', el.text, re.I):
            audit.semantic_warnings.append({
                "page": el.page, "type": "assertion-swallowed-tail", "text": el.text[:220]
            })
        if el.kind == "formula" and re.search(r'[A-Za-zÀ-ÿ]{2,}', el.text):
            audit.semantic_warnings.append({
                "page": el.page, "type": "prose-classified-as-formula", "text": el.text[:220]
            })
        if el.kind == "table" and el.table:
            for ri, row in enumerate(el.table.rows):
                for ci, cell in enumerate(row):
                    empty = cell is None or not ws(cell)
                    # A blank top-left corner is legitimate in row/column-header matrices.
                    legit_corner = (ri == 0 and ci == 0 and len(el.table.rows) > 1 and len(row) > 1
                                    and any(ws(x or "") for x in row[1:])
                                    and any(r and ws(r[0] or "") for r in el.table.rows[1:]))
                    if empty and not legit_corner:
                        audit.semantic_warnings.append({
                            "page": el.page, "type": "empty-table-cell", "row": ri, "col": ci
                        })



# --------------------------- metadata and sections ---------------------------

def detect_metadata(analyzer: PDFAnalyzer, overrides: dict) -> dict:
    title = overrides.get("title")
    area = overrides.get("area")
    author = overrides.get("author")
    if not title or not area:
        p1 = [ln.text for ln in analyzer.page_lines.get(1, []) if not analyzer._is_furniture(ln)]
        # Usually first line(s): area; then title/subarea.
        candidates = [ws(x) for x in p1 if 2 < len(ws(x)) < 100]
        if not area and candidates:
            area = candidates[0].title() if candidates[0].isupper() else candidates[0]
        if not title:
            for c in candidates[1:]:
                if c.casefold() not in {"livro eletrônico", "gran concursos"}:
                    title = c.title() if c.isupper() else c
                    break
    if not author:
        p2 = [ln.text for ln in analyzer.page_lines.get(2, [])]
        # A line in all caps with 2-5 words after the code is a reasonable Gran author candidate.
        for t in p2:
            s = ws(t)
            if re.fullmatch(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{5,60}", s) and 1 <= len(s.split()) <= 6 and not any(k in s for k in ["CÓDIGO", "PRESIDENTE", "GRAN"]):
                author = s.title()
                break
    return {
        "title": title or analyzer.pdf_path.stem,
        "area": area or "",
        "author": author or "",
        "language": "pt-BR",
    }


def slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "secao"


def split_sections(elements: list[Element]) -> list[tuple[str, list[Element]]]:
    """Divide em arquivos por h1 ou blocos grandes conhecidos, evitando um XHTML gigante."""
    sections: list[tuple[str, list[Element]]] = []
    cur_name = "conteudo"
    cur: list[Element] = []
    used = collections.Counter()
    for el in elements:
        if el.kind == "heading" and el.level == 1:
            if cur:
                sections.append((cur_name, cur))
            base = slugify(el.text)[:48]
            used[base] += 1
            cur_name = base if used[base] == 1 else f"{base}-{used[base]}"
            cur = [el]
        else:
            cur.append(el)
    if cur:
        sections.append((cur_name, cur))
    if len(sections) == 1 and sections[0][0] == "conteudo":
        return sections
    return sections


# --------------------------- XHTML rendering ---------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def rich_text(
    s: str,
    color_ranges: Optional[list[ColorRange]] = None,
    bold_ranges: Optional[list[BoldRange]] = None,
    italic_ranges: Optional[list[ItalicRange]] = None,
) -> str:
    """Markup inline conservador; cor, negrito e itálico nunca alteram o texto."""
    colors = normalize_color_ranges(s, color_ranges or [])
    bolds = normalize_bold_ranges(s, bold_ranges or [])
    italics = normalize_italic_ranges(s, italic_ranges or [])
    if not colors and not bolds and not italics:
        return esc(s)

    boundaries = {0, len(s)}
    for cr in colors:
        boundaries.update((cr.start, cr.end))
    for br in bolds:
        boundaries.update((br.start, br.end))
    for ir in italics:
        boundaries.update((ir.start, ir.end))
    cuts = sorted(boundaries)
    out: list[str] = []
    for a, b in zip(cuts, cuts[1:]):
        if b <= a:
            continue
        chunk = esc(s[a:b])
        color_cls = next((cr.css_class for cr in colors if cr.start <= a and b <= cr.end), None)
        is_bold = any(br.start <= a and b <= br.end for br in bolds)
        is_italic = any(ir.start <= a and b <= ir.end for ir in italics)
        if color_cls:
            chunk = f'<span class="{color_cls}">{chunk}</span>'
        if is_italic:
            chunk = f'<em class="pdf-italic">{chunk}</em>'
        if is_bold:
            chunk = f'<strong class="pdf-bold" style="font-weight:800 !important; font-synthesis:weight;">{chunk}</strong>'
        out.append(chunk)
    return "".join(out)


def slice_offset_ranges(ranges: list, start: int, end: int) -> list:
    """Recorta ranges inline para renderizar fragmentos separados por pagebreak."""
    sliced = []
    for item in ranges:
        a, b = max(start, item.start), min(end, item.end)
        if b > a:
            sliced.append(dataclasses.replace(item, start=a - start, end=b - start))
    return sliced


def rich_text_with_pagebreaks(el: Element, include_page_markers: bool = True) -> str:
    """Renderiza marcadores invisíveis dentro do mesmo elemento sem quebrar a prosa."""
    if not include_page_markers or not el.page_breaks:
        return rich_text(el.text, el.color_ranges, el.bold_ranges, el.italic_ranges)
    out = []
    cursor = 0
    for offset, page in sorted(el.page_breaks, key=lambda item: (item[0], item[1])):
        offset = max(cursor, min(offset, len(el.text)))
        out.append(rich_text(
            el.text[cursor:offset],
            slice_offset_ranges(el.color_ranges, cursor, offset),
            slice_offset_ranges(el.bold_ranges, cursor, offset),
            slice_offset_ranges(el.italic_ranges, cursor, offset),
        ))
        out.append(
            f'<span epub:type="pagebreak" class="pagebreak pagebreak-inline" '
            f'id="page-{page}" title="{page}"></span>'
        )
        cursor = offset
    out.append(rich_text(
        el.text[cursor:],
        slice_offset_ranges(el.color_ranges, cursor, len(el.text)),
        slice_offset_ranges(el.bold_ranges, cursor, len(el.text)),
        slice_offset_ranges(el.italic_ranges, cursor, len(el.text)),
    ))
    return "".join(out)


def normalize_table_cell_text(s: str) -> str:
    """Collapse only unambiguous duplicated glyph artifacts in math-like cells.

    This is extraction repair, not mathematical inference. Non-math textual cells are
    left untouched apart from whitespace normalization.
    """
    val = ws(s or "").replace("\n", " ")
    mathlike = bool(re.search(r'[=<>≤≥≠∧∨⋁→↔¬~]|^[pqrPQR𝒑𝒒𝒓𝐩𝐪𝐫]', val))
    if not mathlike:
        return val
    # Repeated mathematical glyphs can be overlay artifacts (e.g. 𝒑𝒑).
    out=[]; i=0
    while i < len(val):
        ch=val[i]
        name=unicodedata.name(ch, '')
        if i+1 < len(val) and val[i+1]==ch and name.startswith('MATHEMATICAL '):
            out.append(ch); i += 2
        else:
            out.append(ch); i += 1
    val=''.join(out).replace('⋁','∨')
    val=unicodedata.normalize('NFKC', val)
    val=re.sub(r'^([pqr])\s+\1(?=\s|[∧∨→↔=<>≤≥≠]|$)', r'\1', val, flags=re.I)
    val=re.sub(r'([=<>≤≥≠]\s*)(\d+)\s+\2\b', r'\1\2', val)
    return val


def render_table(tb: TableRegion) -> str:
    rows = tb.rows
    if not rows:
        return ""
    out = ['<div class="table-wrap"><table>']
    for ri, row in enumerate(rows):
        out.append("<tr>")
        tag = "th" if ri == 0 else "td"
        for cell in row:
            val = normalize_table_cell_text(cell or "")
            out.append(f"<{tag}>{rich_text(val)}</{tag}>")
        out.append("</tr>")
    out.append("</table></div>")
    return "".join(out)


def render_element(el: Element, ids: collections.Counter, page_marker: bool = True) -> tuple[str, Optional[tuple[int, str, str]]]:
    nav = None
    if el.kind == "heading":
        ident_base = slugify(el.text)
        ids[ident_base] += 1
        ident = ident_base if ids[ident_base] == 1 else f"{ident_base}-{ids[ident_base]}"
        level = min(4, max(1, el.level))
        nav = (level, el.text, ident)
        return f'<h{level} id="{ident}">{rich_text(el.text, el.color_ranges, el.bold_ranges, el.italic_ranges)}</h{level}>', nav
    if el.kind == "p":
        return f"<p>{rich_text_with_pagebreaks(el, page_marker)}</p>", None
    if el.kind == "question":
        return f'<p class="question-start"><strong class="question-number">{esc(el.marker)}</strong>&#160;{rich_text_with_pagebreaks(el, page_marker)}</p>', None
    if el.kind == "alternative":
        return f'<p class="alternative"><span class="list-marker">{esc(el.marker)}</span>&#160;{rich_text_with_pagebreaks(el, page_marker)}</p>', None
    if el.kind == "answer":
        return f'<p class="answer">{rich_text(el.text, el.color_ranges, el.bold_ranges, el.italic_ranges)}</p>', None
    if el.kind == "roman":
        return f'<p class="assertion"><span class="list-marker">{esc(el.marker)}</span>&#160;{rich_text_with_pagebreaks(el, page_marker)}</p>', None
    if el.kind in {"bullet", "dash"}:
        # Marcador original em fluxo; sem flex/inline-block, para não fragmentar linhas.
        kind = "subbullet" if el.kind == "bullet" and el.marker in {"◦", "▪", "■", "□", "◆", "◇", "‣", "⁃"} else el.kind
        marker_char = el.marker or ("−" if el.kind == "dash" else "•")
        marker = {"•": "&#x2022;", "−": "&#x2212;", "◦": "&#x25E6;"}.get(marker_char, esc(marker_char))
        return f'<li data-kind="{kind}"><span class="list-marker">{marker}</span>&#160;{rich_text_with_pagebreaks(el, page_marker)}</li>', None
    if el.kind in {"legal", "legal-item", "legal-subitem"}:
        return f'<blockquote class="legal-quote">{render_legal_paragraph(el, page_marker)}</blockquote>', None
    if el.kind == "callout":
        label = el.marker or "Nota"
        cls = slugify(label.rstrip(":"))
        paras = [ws(x) for x in el.text.split("\n") if ws(x)] or [el.text]
        # OBS. é editorialmente inline: "Obs.: conteúdo". Isso também evita a
        # linha curta isolada que amplificava rios de justificação em tela estreita.
        if cls in {"obs", "obs."} or label.casefold().startswith("obs"):
            first = (
                rich_text_with_pagebreaks(el, page_marker)
                if len(paras) == 1
                else rich_text(paras[0])
            )
            rest = "".join(f"<p>{rich_text(p)}</p>" for p in paras[1:])
            return f'<aside class="callout obs"><p><span class="callout-label">{esc(label if label.endswith(":") else label + ":")}</span>&#160;{first}</p>{rest}</aside>', None
        if len(paras) == 1:
            body = f"<p>{rich_text_with_pagebreaks(el, page_marker)}</p>"
        else:
            # Ranges de um bloco multilinha podem perder correspondência ao separar parágrafos.
            # Em caso de dúvida, descarte apenas o estilo inline.
            body = "".join(f"<p>{rich_text(p)}</p>" for p in paras)
        return f'<aside class="callout {cls}"><div class="callout-label">{esc(label)}</div>{body}</aside>', None
    if el.kind == "question-callout":
        return f'<aside class="question-callout">{rich_text_with_pagebreaks(el, page_marker)}</aside>', None
    if el.kind == "image" and el.image and el.image.filename:
        cap = f"<figcaption>{rich_text(el.image.caption)}</figcaption>" if el.image.caption else ""
        return f'<figure><img src="../images/{esc(el.image.filename)}" alt=""/>{cap}</figure>', None
    if el.kind == "table" and el.table:
        return render_table(el.table), None
    return "", None


def render_legal_paragraph(el: Element, page_marker: bool = True) -> str:
    cls = "" if el.kind == "legal" else f' class="{el.kind}"'
    body = rich_text_with_pagebreaks(el, page_marker)
    return f"<p{cls}>{body}</p>"


def render_document(title: str, elements: list[Element], css_href: str = "../styles/book.css", page_marker: bool = True) -> tuple[str, list[tuple[int, str, str]]]:
    ids = collections.Counter()
    navs = []
    body_parts = []
    current_page = None
    i = 0
    while i < len(elements):
        el = elements[i]
        if page_marker and el.page != current_page:
            current_page = el.page
            body_parts.append(f'<span epub:type="pagebreak" class="pagebreak" id="page-{current_page}" title="{current_page}"></span>')
        # Group consecutive bullet/dash items into ul; roman stays paragraph because often assertions.
        if el.kind in {"bullet", "dash"}:
            lis = []
            while i < len(elements) and elements[i].kind in {"bullet", "dash"}:
                item = elements[i]
                li_html, _ = render_element(item, ids, page_marker)
                # Uma lista pode atravessar a quebra física de página. Como o laço
                # agrupa vários itens em um único <ul>, as páginas seguintes não
                # passam pelo marcador emitido no início do laço externo. Insira a
                # âncora no primeiro <li> de cada nova página sem fragmentar a lista.
                if page_marker and item.page != current_page:
                    current_page = item.page
                    marker = (
                        f'<span epub:type="pagebreak" class="pagebreak" '
                        f'id="page-{current_page}" title="{current_page}"></span>'
                    )
                    opening_end = li_html.find(">") + 1
                    li_html = li_html[:opening_end] + marker + li_html[opening_end:]
                if item.page_breaks:
                    current_page = item.page_breaks[-1][1]
                lis.append(li_html)
                i += 1
            body_parts.append('<ul class="bullets">' + "".join(lis) + "</ul>")
            continue
        # Um artigo e seus parágrafos/incisos formam uma única citação normativa.
        # Blocos separados reiniciam a borda e somam margens verticais entre unidades
        # que pertencem ao mesmo excerto legal.
        if el.kind in {"legal", "legal-item", "legal-subitem"}:
            paragraphs = []
            while i < len(elements) and elements[i].kind in {"legal", "legal-item", "legal-subitem"}:
                item = elements[i]
                paragraph = render_legal_paragraph(item, page_marker)
                if page_marker and item.page != current_page:
                    current_page = item.page
                    marker = (
                        f'<span epub:type="pagebreak" class="pagebreak" '
                        f'id="page-{current_page}" title="{current_page}"></span>'
                    )
                    opening_end = paragraph.find(">") + 1
                    paragraph = paragraph[:opening_end] + marker + paragraph[opening_end:]
                if item.page_breaks:
                    current_page = item.page_breaks[-1][1]
                paragraphs.append(paragraph)
                i += 1
            body_parts.append('<blockquote class="legal-quote">' + "".join(paragraphs) + "</blockquote>")
            continue
        frag, nav = render_element(el, ids, page_marker)
        if frag:
            body_parts.append(frag)
        if nav:
            navs.append(nav)
        if el.page_breaks:
            current_page = el.page_breaks[-1][1]
        i += 1
    xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<!-- Gran EPUB Pipeline {VERSION} -->
<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="pt-BR" xml:lang="pt-BR">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title><link href="{css_href}" rel="stylesheet" type="text/css"/></head>
<body>{''.join(body_parts)}</body></html>'''
    return xhtml, navs


# --------------------------- EPUB packaging ---------------------------

class EPUBWriter:
    def __init__(self, work: Path, meta: dict, config: Config, audit: Audit):
        self.work = work
        self.meta = meta
        self.config = config
        self.audit = audit
        self.oebps = work / "OEBPS"
        self.text_dir = self.oebps / "text"
        self.style_dir = self.oebps / "styles"
        self.image_dir = self.oebps / "images"
        self.text_dir.mkdir(parents=True, exist_ok=True)
        self.style_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.manifest: list[tuple[str, str, str, str]] = []  # id, href, mime, properties
        self.spine: list[str] = []
        self.nav_entries: list[tuple[int, str, str, str]] = []  # level, title, file, id
        self.page_entries: list[tuple[int, str]] = []
        self.uid = f"urn:uuid:{uuid.uuid4()}"

    def add_base(self, css_path: Path, cover_path: Path):
        (self.work / "mimetype").write_text("application/epub+zip", encoding="ascii")
        meta_inf = self.work / "META-INF"
        meta_inf.mkdir(exist_ok=True)
        (meta_inf / "container.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''', encoding="utf-8")
        shutil.copy2(css_path, self.style_dir / "book.css")
        shutil.copy2(cover_path, self.image_dir / "cover.png")
        self.manifest.append(("css", "styles/book.css", "text/css", ""))
        self.manifest.append(("cover-image", "images/cover.png", "image/png", "cover-image"))

    def add_titlepage(self):
        area = html.escape(self.meta.get("area", ""))
        title = html.escape(self.meta["title"])
        author = html.escape(self.meta.get("author", ""))
        cover = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html><html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="pt-BR" xml:lang="pt-BR"><head><meta charset="utf-8"/><title>Capa</title><link href="../styles/book.css" rel="stylesheet"/></head><body><figure class="cover"><img src="../images/cover.png" alt="{title}"/></figure></body></html>'''
        titlep = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html><html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="pt-BR" xml:lang="pt-BR"><head><meta charset="utf-8"/><title>Folha de rosto</title><link href="../styles/book.css" rel="stylesheet"/></head><body class="titlepage"><p class="area">{area}</p><h1>{title}</h1><p class="author">{author}</p></body></html>'''
        (self.text_dir / "cover.xhtml").write_text(cover, encoding="utf-8")
        (self.text_dir / "title.xhtml").write_text(titlep, encoding="utf-8")
        self.manifest += [
            ("cover", "text/cover.xhtml", "application/xhtml+xml", ""),
            ("title", "text/title.xhtml", "application/xhtml+xml", ""),
        ]
        self.spine += ["cover", "title"]

    def add_section(self, name: str, elements: list[Element]):
        xhtml, navs = render_document(self.meta["title"], elements, page_marker=self.config.keep_page_markers)
        fn = f"{name}.xhtml"
        (self.text_dir / fn).write_text(xhtml, encoding="utf-8")
        iid = f"sec-{slugify(name)}"
        self.manifest.append((iid, f"text/{fn}", "application/xhtml+xml", ""))
        self.spine.append(iid)
        for lvl, t, anchor in navs:
            self.nav_entries.append((lvl, t, fn, anchor))
        for el in elements:
            element_pages = [el.page] + [page for _, page in el.page_breaks]
            for page in element_pages:
                if self.config.keep_page_markers and not any(p == page and f == fn for p, f in self.page_entries):
                    self.page_entries.append((page, fn))

    def add_images(self):
        for p in sorted(self.image_dir.glob("*")):
            if p.name == "cover.png":
                continue
            ext = p.suffix.lower()
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}.get(ext)
            if mime:
                self.manifest.append((f"img-{slugify(p.stem)}", f"images/{p.name}", mime, ""))

    def write_nav(self):
        lis = []
        for lvl, title, fn, anchor in self.nav_entries:
            lis.append(f'<li class="toc-l{lvl}"><a href="{fn}#{anchor}">{html.escape(title)}</a></li>')
        pages = []
        seen = set()
        for p, fn in self.page_entries:
            if p in seen:
                continue
            seen.add(p)
            pages.append(f'<li><a href="{fn}#page-{p}">{p}</a></li>')
        nav = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html><html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="pt-BR" xml:lang="pt-BR"><head><meta charset="utf-8"/><title>Sumário</title><link href="../styles/book.css" rel="stylesheet"/></head><body><nav epub:type="toc" id="toc"><h1>Sumário</h1><ol>{''.join(lis)}</ol></nav><nav epub:type="page-list" hidden="hidden"><ol>{''.join(pages)}</ol></nav></body></html>'''
        (self.text_dir / "nav.xhtml").write_text(nav, encoding="utf-8")
        self.manifest.append(("nav", "text/nav.xhtml", "application/xhtml+xml", "nav"))
        # Place navigation after title page.
        if "nav" not in self.spine:
            self.spine.insert(2, "nav")

    def write_opf(self):
        creator = html.escape(self.meta.get("author", ""))
        creator_xml = f"<dc:creator>{creator}</dc:creator>" if creator else ""
        title = html.escape(self.meta["title"])
        manifest = "\n".join(
            f'<item id="{iid}" href="{href}" media-type="{mime}"' + (f' properties="{prop}"' if prop else "") + "/>"
            for iid, href, mime, prop in self.manifest
        )
        spine = "\n".join(f'<itemref idref="{iid}"/>' for iid in self.spine)
        opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="{OPF_NS}" version="3.0" unique-identifier="pub-id" xml:lang="pt-BR">
<metadata xmlns:dc="{DC_NS}"><dc:identifier id="pub-id">{self.uid}</dc:identifier><dc:title>{title}</dc:title><dc:language>pt-BR</dc:language>{creator_xml}<meta property="dcterms:modified">{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</meta></metadata>
<manifest>{manifest}</manifest><spine>{spine}</spine></package>'''
        (self.oebps / "content.opf").write_text(opf, encoding="utf-8")

    def validate_xml(self):
        # XML parser from stdlib: catches malformed XML/XHTML without extra dependency.
        import xml.etree.ElementTree as ET
        for p in self.work.rglob("*"):
            if p.suffix.lower() in {".xhtml", ".xml", ".opf"}:
                try:
                    ET.parse(p)
                except Exception as exc:
                    raise RuntimeError(f"XML inválido em {p}: {exc}") from exc

    def package(self, output: Path):
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w") as z:
            z.write(self.work / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
            for p in sorted(self.work.rglob("*")):
                if p.is_dir() or p == self.work / "mimetype":
                    continue
                z.write(p, p.relative_to(self.work).as_posix(), compress_type=zipfile.ZIP_DEFLATED)


def validate_epub_archive(epub: Path) -> dict[str, object]:
    """Validação interna mínima e independente de Java; não substitui o EPUBCheck."""
    import xml.etree.ElementTree as ET

    errors: list[str] = []
    parsed_xml = 0
    entry_count = 0
    opf_identifier: Optional[str] = None
    ncx_identifier: Optional[str] = None
    try:
        with zipfile.ZipFile(epub) as z:
            infos = z.infolist()
            entry_count = len(infos)
            names = [info.filename for info in infos]
            duplicates = sorted(name for name, count in collections.Counter(names).items() if count > 1)
            if duplicates:
                errors.append("Entradas ZIP duplicadas: " + ", ".join(duplicates))
            if not infos or infos[0].filename != "mimetype":
                errors.append("A primeira entrada ZIP não é mimetype.")
            else:
                if infos[0].compress_type != zipfile.ZIP_STORED:
                    errors.append("A entrada mimetype está comprimida.")
                if z.read(infos[0]) != b"application/epub+zip":
                    errors.append("Conteúdo inválido na entrada mimetype.")
            bad_entry = z.testzip()
            if bad_entry:
                errors.append(f"CRC inválido na entrada ZIP: {bad_entry}")
            for info in infos:
                if Path(info.filename).suffix.lower() not in {".xhtml", ".xml", ".opf", ".ncx", ".svg"}:
                    continue
                try:
                    root = ET.fromstring(z.read(info))
                    parsed_xml += 1
                    suffix = Path(info.filename).suffix.lower()
                    if suffix == ".opf":
                        for element in root.iter():
                            if element.tag.rsplit("}", 1)[-1].lower() == "identifier" and element.text:
                                opf_identifier = ws(element.text)
                                break
                    elif suffix == ".ncx":
                        for element in root.iter():
                            if (element.tag.rsplit("}", 1)[-1].lower() == "meta"
                                    and element.attrib.get("name") == "dtb:uid"):
                                ncx_identifier = ws(element.attrib.get("content", ""))
                                break
                    if Path(info.filename).suffix.lower() == ".xhtml":
                        forbidden_u = False
                        forbidden_style = False
                        decorated_verified_phrase = False
                        spaced_fragments: list[str] = []
                        text_blocks = {
                            "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "figcaption"
                        }
                        for element in root.iter():
                            local_name = element.tag.rsplit("}", 1)[-1].lower()
                            if local_name == "u":
                                forbidden_u = True
                            style = str(element.attrib.get("style", ""))
                            if re.search(r"text-decoration(?:-line)?\s*:\s*[^;]*underline", style, re.I):
                                forbidden_style = True
                            if local_name == "a":
                                anchor_text = ws("".join(element.itertext()))
                                if anchor_text == "Autorização do Banco Central":
                                    decorated_verified_phrase = True
                            if local_name in text_blocks:
                                element_text = ws("".join(element.itertext()))
                                for match in SPACED_WORD_FRAGMENT_RE.finditer(element_text):
                                    spaced_fragments.append(match.group(0))
                        if forbidden_u:
                            errors.append(f"Sublinhado textual proibido (<u>) em {info.filename}.")
                        if forbidden_style:
                            errors.append(f"Sublinhado inline proibido em {info.filename}.")
                        if decorated_verified_phrase:
                            errors.append(
                                f"Trecho verificado não pode ser hyperlink/sublinhado em {info.filename}: "
                                "Autorização do Banco Central."
                            )
                        if spaced_fragments:
                            examples = ", ".join(repr(value) for value in sorted(set(spaced_fragments))[:5])
                            errors.append(
                                f"Possível palavra fragmentada por hífen físico em {info.filename}: {examples}. "
                                "Confira o PDF antes de corrigir."
                            )
                        for element in root.iter():
                            local_name = element.tag.rsplit("}", 1)[-1].lower()
                            if local_name not in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
                                continue
                            element_text = ws("".join(element.itertext()))
                            if not QUESTION_RE.match(element_text):
                                continue
                            classes = set(str(element.attrib.get("class", "")).split())
                            if local_name != "p" or "question-start" not in classes:
                                number = element_text[:4]
                                errors.append(
                                    f"Questão {number} sem marcação p.question-start em {info.filename}."
                                )
                        for parent in root.iter():
                            if parent.tag.rsplit("}", 1)[-1].lower() != "body":
                                continue
                            previous_was_legal = False
                            for child in list(parent):
                                local_name = child.tag.rsplit("}", 1)[-1].lower()
                                classes = set(str(child.attrib.get("class", "")).split())
                                if local_name == "span" and "pagebreak" in classes:
                                    continue
                                is_legal = local_name == "blockquote" and "legal-quote" in classes
                                if is_legal and previous_was_legal:
                                    errors.append(
                                        f"Citações legais consecutivas não agrupadas em {info.filename}."
                                    )
                                    break
                                previous_was_legal = is_legal
                            break
                except Exception as exc:
                    errors.append(f"XML inválido em {info.filename}: {exc}")
            if opf_identifier and ncx_identifier and opf_identifier != ncx_identifier:
                errors.append(
                    f"Identificador NCX ({ncx_identifier}) difere do identificador OPF ({opf_identifier})."
                )
    except Exception as exc:
        errors.append(f"Não foi possível abrir o EPUB: {exc}")
    return {
        "status": "passed" if not errors else "failed",
        "scope": "ZIP, mimetype, CRC, XML, identificadores OPF/NCX, questões numeradas, agrupamento legal, palavras possivelmente fragmentadas e ausência de sublinhado textual inventado; não substitui o EPUBCheck",
        "entry_count": entry_count,
        "xml_files_parsed": parsed_xml,
        "errors": errors,
    }


def resolve_epubcheck_command() -> tuple[Optional[list[str]], str]:
    script_dir = Path(__file__).resolve().parent
    jar = script_dir / "tools" / f"epubcheck-{EPUBCHECK_VERSION}" / "epubcheck.jar"
    java = shutil.which("java")
    if jar.is_file() and java:
        return [java, "-jar", str(jar)], f"bundled {EPUBCHECK_VERSION}"
    exe = shutil.which("epubcheck")
    if exe:
        return [exe], "PATH"
    if jar.is_file() and not java:
        return None, f"EPUBCheck {EPUBCHECK_VERSION} incluído, mas Java não está disponível"
    return None, "EPUBCheck não encontrado no pacote nem no PATH"


def run_epubcheck(epub: Path, audit: Audit) -> dict[str, object]:
    command, source = resolve_epubcheck_command()
    if not command:
        result: dict[str, object] = {
            "status": "unavailable",
            "executed": False,
            "source": source,
            "note": "A validação interna foi executada, mas não substitui o EPUBCheck.",
        }
        audit.epubcheck = result
        audit.warnings.append(source + "; EPUBCheck não foi executado.")
        return result
    try:
        proc = subprocess.run(
            [*command, str(epub)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
            timeout=300,
        )
        output = proc.stdout.strip()
        result = {
            "status": "passed" if proc.returncode == 0 else "failed",
            "executed": True,
            "source": source,
            "returncode": proc.returncode,
            "output": output[-12000:],
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "executed": True,
            "source": source,
            "returncode": None,
            "output": str(exc),
        }
    audit.epubcheck = result
    if result["status"] == "failed":
        audit.warnings.append("EPUBCheck falhou:\n" + str(result.get("output", ""))[-4000:])
    return result


def validate_epub(epub: Path, report_path: Optional[Path] = None) -> dict[str, object]:
    audit = Audit()
    audit.archive_validation = validate_epub_archive(epub)
    epubcheck = run_epubcheck(epub, audit)
    if audit.archive_validation["status"] == "failed" or epubcheck["status"] == "failed":
        status = "failed"
    elif epubcheck["status"] == "passed":
        status = "passed"
    else:
        status = "partial"
    report = {
        "pipeline_version": VERSION,
        "file": str(epub),
        "status": status,
        "audit": dataclasses.asdict(audit),
    }
    rp = report_path or epub.with_suffix(".validation.json")
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


CANONICAL_QUESTION_LINE_EXCLUSIONS = [
    {
        # Exceção por texto completo, não por geometria aproximada nem hash do arquivo.
        # A ausência de página torna a regra robusta a repaginações binariamente distintas
        # do mesmo material, sem alcançar frases diferentes.
        "pattern": r"^Professor, o que acontece se uma Instituição Financeira, nacional ou estrangeira, iniciar$",
    },
]


def apply_builtin_overrides(pdf: Path, overrides: dict) -> dict:
    """Aplica exceções canônicas verificadas, sempre estreitas pelo texto completo."""
    merged = dict(overrides)
    exclusions = list(overrides.get("question_box_line_exclusions", []))
    for rule in CANONICAL_QUESTION_LINE_EXCLUSIONS:
        if rule not in exclusions:
            exclusions.append(dict(rule))
    merged["question_box_line_exclusions"] = exclusions
    return merged


def load_overrides(path: Optional[Path]) -> dict:
    if not path:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def convert(pdf: Path, output: Path, css: Path, overrides_path: Optional[Path] = None, report_path: Optional[Path] = None):
    require_conversion_dependencies()
    # O nome do EPUB é invariavelmente o mesmo basename do PDF de entrada;
    # um caminho de saída fornecido serve apenas para escolher o diretório.
    expected_name = pdf.with_suffix(".epub").name
    if output.exists() and output.is_dir():
        output = output / expected_name
    elif output.suffix.lower() == ".epub":
        output = output.parent / expected_name
    else:
        output = output / expected_name
    output.parent.mkdir(parents=True, exist_ok=True)

    overrides = apply_builtin_overrides(pdf, load_overrides(overrides_path))
    cfg = Config()
    for k, v in overrides.get("config", {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    audit = Audit()
    analyzer = PDFAnalyzer(pdf, cfg, overrides, audit)
    page_count = len(analyzer.doc)
    try:
        analyzer.extract_all_lines()
        analyzer.detect_repeating_furniture()
        analyzer.detect_question_boxes()
        analyzer.detect_tables()
        analyzer.repair_repeated_table_cells()
        analyzer.audit_table_geometry()
        analyzer.detect_images()
        meta = detect_metadata(analyzer, overrides)
        semantic = SemanticBuilder(analyzer, audit, overrides)
        elements = semantic.all_elements()
        qa_semantic_structure(elements, audit)
        sections = split_sections(elements)

        with tempfile.TemporaryDirectory(prefix="gran_epub_") as td:
            work = Path(td)
            writer = EPUBWriter(work, meta, cfg, audit)
            cover = work / "cover.png"
            analyzer.render_cover(cover)
            writer.add_base(css, cover)
            writer.add_titlepage()
            analyzer.render_images(writer.image_dir)
            writer.add_images()
            for name, els in sections:
                writer.add_section(name, els)
            writer.write_nav()
            writer.write_opf()
            writer.validate_xml()
            writer.package(output)
        audit.archive_validation = validate_epub_archive(output)
        run_epubcheck(output, audit)
    finally:
        analyzer.close()

    report = {
        "pipeline_version": VERSION,
        "source": str(pdf),
        "output": str(output),
        "metadata": meta,
        "pages": page_count,
        "body_font_size_estimate": analyzer.body_size,
        "audit": dataclasses.asdict(audit),
    }
    rp = report_path or output.with_suffix(".audit.json")
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if audit.archive_validation.get("status") == "failed" or audit.epubcheck.get("status") == "failed":
        raise RuntimeError(f"O EPUB não passou na validação. Consulte {rp}.")
    return report


def inspect_pdf(pdf: Path, out: Path):
    """Gera inventário de linhas, bboxes, estilos e ranges cromáticos/negrito para auditoria."""
    require_conversion_dependencies()
    audit = Audit(); cfg = Config(); overrides = apply_builtin_overrides(pdf, {})
    a = PDFAnalyzer(pdf, cfg, overrides, audit)
    try:
        a.extract_all_lines(); a.detect_repeating_furniture(); a.detect_question_boxes(); a.detect_tables(); a.repair_repeated_table_cells(); a.audit_table_geometry(); a.detect_images()
        data = {
            "body_size": a.body_size,
            "repeated_furniture": sorted(a.repeated_keys),
            "pages": {},
        }
        for pno, lines in a.page_lines.items():
            data["pages"][str(pno)] = {
                "lines": [dataclasses.asdict(x) for x in lines],
                "images": [dataclasses.asdict(x) for x in a.page_images.get(pno, [])],
                "tables": [dataclasses.asdict(x) for x in a.page_tables.get(pno, [])],
            }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        a.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Gran PDF -> EPUB 3 refluível")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="converter PDF para EPUB")
    c.add_argument("pdf", type=Path)
    c.add_argument("epub", type=Path, nargs="?", help="arquivo ou diretório de saída; o basename será sempre o mesmo do PDF")
    c.add_argument("--css", type=Path, default=Path(__file__).with_name("Gran_EPUB_Estilos.css"))
    c.add_argument("--overrides", type=Path)
    c.add_argument("--report", type=Path)
    i = sub.add_parser("inspect", help="gerar inventário estrutural JSON")
    i.add_argument("pdf", type=Path)
    i.add_argument("json", type=Path)
    v = sub.add_parser("validate", help="validar um EPUB e gerar relatório JSON")
    v.add_argument("epub", type=Path)
    v.add_argument("--report", type=Path)
    args = ap.parse_args()
    if args.cmd == "convert":
        target = args.epub if args.epub is not None else args.pdf.with_suffix(".epub")
        report = convert(args.pdf, target, args.css, args.overrides, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.cmd == "inspect":
        inspect_pdf(args.pdf, args.json)
        print(args.json)
    else:
        report = validate_epub(args.epub, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["status"] == "failed":
            raise SystemExit(1)
        if report["status"] == "partial":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
