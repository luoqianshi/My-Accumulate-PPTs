#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 论文图片提取器

功能:
1. 从学术论文 PDF 中提取关键图片/图表（过滤小图标、Logo、作者照片、页眉页脚装饰）
2. 支持矢量图表检测与页面裁剪提取（含透明背景）
3. 智能去重：避免同一图表被重复提取

用法:
    python pdf_extractor.py <pdf_path> [--output-dir <dir>]

依赖:
    pip install pymupdf Pillow
"""

import os
import sys
import argparse
import json
import shutil
from pathlib import Path

import fitz  # PyMuPDF



def is_likely_figure_or_table(img_info, page_width, page_height):
    """
    根据图片尺寸、面积、宽高比和在页面上的位置，判断其是否可能是论文中的有效图表。
    过滤掉作者照片、小图标、装饰线、Logo 等。
    """
    w, h = img_info['width'], img_info['height']
    area = w * h
    aspect = w / h if h > 0 else 999
    bbox = img_info.get('bbox')  # 页面坐标 (x0, y0, x1, y1)

    # 绝对尺寸阈值：太小的不要
    if w < 150 or h < 150:
        return False, "too_small"

    # 面积阈值
    if area < 30000:
        return False, "too_small_area"

    # 过滤极端细长的装饰线（宽高比 > 20 或 < 0.05）
    if aspect > 20 or aspect < 0.05:
        return False, "extreme_aspect_ratio"

    # 过滤超宽但高度很小的图片（可能是页眉/页脚横线）
    if w > 2000 and h < 100:
        return False, "likely_separator_line"

    # 位置感知过滤：正方形或接近正方形的图片
    if 0.8 <= aspect <= 1.25:
        # 如果面积小于阈值，很可能是作者头像或会议 Logo
        if area < 350000:
            return False, "likely_author_photo_or_logo"

        # 即使面积大，如果位于页面顶部或底部边缘，也可能是 Logo
        if bbox:
            y_center = (bbox[1] + bbox[3]) / 2
            page_h = page_height
            # 页面顶部 15% 或底部 15% 区域
            if y_center < page_h * 0.15 or y_center > page_h * 0.85:
                return False, "likely_header_footer_logo"

    return True, "ok"


def extract_embedded_images(doc):
    """
    扫描 PDF 中内嵌的图像对象，过滤后仅返回元数据（不保存图片文件）。
    利用页面上图片的 bbox 信息辅助判断，供后续裁剪去重使用。
    返回提取的图片元数据列表。
    """
    extracted = []
    seen_xrefs = set()

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        image_list = page.get_images(full=True)
        pw, ph = page.rect.width, page.rect.height

        # 获取页面上所有图片的位置信息
        img_bboxes = {}
        try:
            for info in page.get_image_info():
                xref = info.get("xref")
                bbox = info.get("bbox")
                if xref and bbox:
                    img_bboxes.setdefault(xref, []).append(bbox)
        except Exception:
            pass

        for img in image_list:
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base_image = doc.extract_image(xref)
                iw = base_image.get("width", 0)
                ih = base_image.get("height", 0)

                # 获取该图片在页面上的位置（取第一个实例）
                bbox = None
                if xref in img_bboxes and img_bboxes[xref]:
                    b = img_bboxes[xref][0]
                    bbox = (b.x0, b.y0, b.x1, b.y1)

                img_info = {"width": iw, "height": ih, "bbox": bbox}
                is_valid, reason = is_likely_figure_or_table(img_info, pw, ph)
                if not is_valid:
                    print(f"  [过滤] 第 {page_num+1} 页 xref={xref} ({iw}x{ih}): {reason}")
                    continue

                extracted.append({
                    "page": page_num + 1,
                    "width": iw,
                    "height": ih,
                    "source": "embedded_image",
                    "bbox": bbox,
                })

            except Exception as e:
                print(f"  [警告] 提取第 {page_num+1} 页图片 xref={xref} 时出错: {e}")
                continue

    return extracted


def detect_figure_regions_by_drawings(page, min_area=5000, padding=3):
    """
    通过分析页面的矢量绘制命令来检测可能的图表区域。
    返回页面坐标系中的矩形区域列表 (x0, y0, x1, y1)。
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    rects = []
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        if rect.width < 50 or rect.height < 50:
            continue
        if rect.width * rect.height < min_area:
            continue
        r = fitz.Rect(
            max(0, rect.x0 - padding),
            max(0, rect.y0 - padding),
            min(page.rect.width, rect.x1 + padding),
            min(page.rect.height, rect.y1 + padding),
        )
        rects.append(r)

    # 合并重叠矩形（迭代直到稳定）
    merged = []
    for r in rects:
        found = False
        for m in merged:
            if m.intersects(r):
                m |= r
                found = True
                break
        if not found:
            merged.append(fitz.Rect(r))

    changed = True
    while changed:
        changed = False
        new_merged = []
        for r in merged:
            found = False
            for m in new_merged:
                if m.intersects(r):
                    m |= r
                    found = True
                    changed = True
                    break
            if not found:
                new_merged.append(fitz.Rect(r))
        merged = new_merged

    return merged


def detect_figure_regions_by_images(page):
    """根据页面上图片的放置位置(bbox)确定区域。"""
    regions = []
    for img_dict in page.get_image_info():
        bbox = img_dict.get("bbox")
        if bbox:
            regions.append(fitz.Rect(bbox))
    return regions


def detect_figure_regions_by_captions(page):
    """
    检测页面中包含 'Figure', 'Fig.', 'Table' 等字样的区域，
    返回这些标题本身的 bbox 列表（用于辅助验证，不直接作为图表区域）。
    """
    captions = []
    text_blocks = page.get_text("blocks")
    for b in text_blocks:
        if len(b) < 7:
            continue
        x0, y0, x1, y1, text, block_no, block_type = b[:7]
        text_upper = text.strip().upper()
        # 更严格的匹配：确保是图表标题而非其他包含这些词的文本
        if (text_upper.startswith("FIGURE") or text_upper.startswith("FIG.") or
                text_upper.startswith("TABLE") or text_upper.startswith("ALGORITHM")):
            captions.append(fitz.Rect(x0, y0, x1, y1))
    return captions


def detect_non_text_regions(page, text_coverage_threshold=0.15):
    """
    检测文本覆盖率较低但存在大面积绘制内容的区域。
    """
    text_blocks = page.get_text("blocks")
    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    draw_rects = []
    for d in drawings:
        rect = d.get("rect")
        if rect and rect.width > 50 and rect.height > 50:
            draw_rects.append(rect)

    if not draw_rects:
        return []

    text_rects = []
    for b in text_blocks:
        if len(b) >= 6:
            text_rects.append(fitz.Rect(b[0], b[1], b[2], b[3]))

    candidate_regions = []
    for dr in draw_rects:
        area = dr.width * dr.height
        text_area = 0
        for tr in text_rects:
            inter = dr & tr
            if inter:
                text_area += inter.width * inter.height
        coverage = text_area / area if area > 0 else 1
        if coverage < text_coverage_threshold and area > 10000:
            candidate_regions.append(dr)

    merged = []
    for r in candidate_regions:
        found = False
        for m in merged:
            if m.intersects(r):
                m |= r
                found = True
                break
        if not found:
            merged.append(fitz.Rect(r))

    return merged


def merge_overlapping_regions(regions, overlap_threshold=0.5):
    """合并重叠率超过阈值的矩形区域。"""
    if not regions:
        return []

    merged = []
    for r in regions:
        found = False
        for m in merged:
            inter = m & r
            if inter:
                inter_area = inter.width * inter.height
                min_area = min(m.width * m.height, r.width * r.height)
                if min_area > 0 and inter_area / min_area > overlap_threshold:
                    m |= r
                    found = True
                    break
        if not found:
            merged.append(fitz.Rect(r))

    # 迭代直到稳定
    changed = True
    while changed:
        changed = False
        new_merged = []
        for r in merged:
            found = False
            for m in new_merged:
                inter = m & r
                if inter:
                    inter_area = inter.width * inter.height
                    min_area = min(m.width * m.height, r.width * r.height)
                    if min_area > 0 and inter_area / min_area > overlap_threshold:
                        m |= r
                        found = True
                        changed = True
                        break
            if not found:
                new_merged.append(fitz.Rect(r))
        merged = new_merged

    return merged


def extract_figures_by_region_cropping(doc, output_dir, dpi=200,
                                       min_region_width=100, min_region_height=100,
                                       max_regions_per_page=5):
    """
    通过区域检测+裁剪的方式提取矢量图表。
    综合使用 drawing 检测、图片放置位置和非文本区域检测。
    包含多层过滤以排除页眉、页脚、空白区域和整页误检。
    仅保存裁剪后的图片，不保留中间文件。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted = []
    img_counter = 1
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pw, ph = page.rect.width, page.rect.height
        page_area = pw * ph

        # 收集候选区域
        regions = []
        regions.extend(detect_figure_regions_by_drawings(page))
        regions.extend(detect_figure_regions_by_images(page))
        regions.extend(detect_non_text_regions(page))

        # 合并重叠区域
        regions = merge_overlapping_regions(regions, overlap_threshold=0.3)

        # 过滤候选区域
        filtered = []
        for r in regions:
            # 1. 尺寸过滤（页面坐标系）
            if r.width < 40 or r.height < 40:
                continue

            # 2. 面积过滤：不能超过页面面积的 75%（避免裁剪整页）
            if r.width * r.height > page_area * 0.75:
                continue

            # 3. 位置过滤：排除页眉（y1 < 60pt）和页脚（y0 > ph * 0.95）
            if r.y1 < 60 or r.y0 > ph * 0.95:
                continue

            # 4. 像素尺寸过滤
            rw = r.width * zoom
            rh = r.height * zoom
            if rw < min_region_width or rh < min_region_height:
                continue

            # 5. 宽高比过滤
            aspect = rw / rh if rh > 0 else 999
            if aspect > 20 or aspect < 0.05:
                continue

            # 6. 避免与已有区域过度重叠
            overlap = False
            for existing in filtered:
                inter = existing & r
                if inter:
                    inter_area = inter.width * inter.height
                    min_area = min(existing.width * existing.height, r.width * r.height)
                    if min_area > 0 and inter_area / min_area > 0.7:
                        overlap = True
                        break
            if not overlap:
                filtered.append(fitz.Rect(r))

        # 按面积排序并限制数量
        filtered.sort(key=lambda r: r.width * r.height, reverse=True)
        filtered = filtered[:max_regions_per_page]

        for idx, r in enumerate(filtered, 1):
            try:
                pix = page.get_pixmap(matrix=mat, clip=r, alpha=True)
                output_path = output_dir / f"crop_{img_counter:03d}_page{page_num+1:03d}_reg{idx}.png"
                pix.save(output_path)

                # 7. 文件大小过滤：裁剪后文件 < 3KB 的视为空白/无意义区域，丢弃
                file_size = output_path.stat().st_size
                if file_size < 3072:
                    print(f"  [过滤] 第 {page_num+1} 页区域 {output_path.name} 文件过小 ({file_size} 字节)，可能是空白区域，丢弃")
                    output_path.unlink()
                    continue

                extracted.append({
                    "path": str(output_path),
                    "page": page_num + 1,
                    "width": pix.width,
                    "height": pix.height,
                    "source": "region_crop",
                    "bbox": (r.x0, r.y0, r.x1, r.y1),
                })
                img_counter += 1
            except Exception as e:
                print(f"  [警告] 裁剪第 {page_num+1} 页区域时出错: {e}")
                continue

    return extracted


def remove_duplicate_extractions(embedded, cropped, iou_threshold=0.6):
    """
    移除裁剪区域中与内嵌图片高度重叠的重复项。
    返回去重后的裁剪列表（不合并内嵌图片，仅用于去重参考）。
    """
    # 对于每个裁剪区域，检查是否与任何内嵌图片的 bbox 有高度重叠
    # 注意：内嵌图片的 bbox 是页面坐标，裁剪区域也有 bbox
    filtered_cropped = []
    for c in cropped:
        c_bbox = c.get("bbox")
        if c_bbox is None:
            filtered_cropped.append(c)
            continue

        cx0, cy0, cx1, cy1 = c_bbox
        c_area = (cx1 - cx0) * (cy1 - cy0)
        if c_area <= 0:
            filtered_cropped.append(c)
            continue

        is_dup = False
        for e in embedded:
            e_bbox = e.get("bbox")
            if e_bbox is None:
                continue
            ex0, ey0, ex1, ey1 = e_bbox
            e_area = (ex1 - ex0) * (ey1 - ey0)
            if e_area <= 0:
                continue

            ix0 = max(cx0, ex0)
            iy0 = max(cy0, ey0)
            ix1 = min(cx1, ex1)
            iy1 = min(cy1, ey1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue

            inter_area = (ix1 - ix0) * (iy1 - iy0)
            union_area = c_area + e_area - inter_area
            iou = inter_area / union_area if union_area > 0 else 0

            if iou > iou_threshold:
                is_dup = True
                c_name = Path(c['path']).name if 'path' in c else "unknown"
                print(f"  [去重] 裁剪区域 {c_name} 与第 {e.get('page', '?')} 页内嵌图片重叠 (IoU={iou:.2f})，跳过")
                break

        if not is_dup:
            filtered_cropped.append(c)

    return filtered_cropped


def render_pages_as_fallback(doc, output_dir, dpi=200):
    """将每一页渲染为高清图片。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        output_path = output_dir / f"page_{page_num+1:03d}_{dpi}dpi.png"
        pix.save(output_path)
        rendered.append(str(output_path))

    return rendered


def main():
    parser = argparse.ArgumentParser(description="从学术论文 PDF 中提取图片和文本")
    parser.add_argument("pdf_path", help="输入 PDF 文件路径")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录（默认与 PDF 同名文件夹）",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="渲染分辨率 DPI（默认 200）",
    )
    parser.add_argument(
        "--no-region-crop",
        action="store_true",
        help="跳过矢量图表区域裁剪",
    )
    parser.add_argument(
        "--render-pages",
        action="store_true",
        help="同时渲染每一页为高清图片（后备方案）",
    )
    parser.add_argument(
        "--max-regions-per-page",
        type=int,
        default=5,
        help="每页最多裁剪区域数（默认 5）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="清空输出目录后再提取",
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf_path).resolve()
    if not pdf_path.exists():
        print(f"[错误] 文件不存在: {pdf_path}")
        sys.exit(1)

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = Path("d:/Data/New_Codes/SKILLS/Accumulate-PPTs/paper-slides") / pdf_path.stem

    if args.clean and output_dir.exists():
        print(f"[信息] 清空输出目录: {output_dir}")
        shutil.rmtree(output_dir)

    print(f"[信息] 处理 PDF: {pdf_path}")
    print(f"[信息] 输出目录: {output_dir}")
    print("-" * 50)

    doc = fitz.open(str(pdf_path))

    # 1. 扫描内嵌图片（仅用于获取元数据，不保存文件）
    print("[步骤 1/3] 扫描内嵌图片（用于去重参考）...")
    embedded = extract_embedded_images(doc)
    print(f"[结果] 扫描到 {len(embedded)} 张有效内嵌图片（仅作去重参考，不保存）")

    # 2. 区域裁剪提取矢量图表（仅保留裁剪图片）
    cropped = []
    if not args.no_region_crop:
        print("\n[步骤 2/3] 检测并裁剪矢量图表区域...")
        cropped = extract_figures_by_region_cropping(
            doc,
            output_dir,
            dpi=args.dpi,
            max_regions_per_page=args.max_regions_per_page,
        )
        print(f"[结果] 裁剪到 {len(cropped)} 个图表区域")
        for img in cropped:
            print(f"  - {Path(img['path']).name} (第 {img['page']} 页, {img['width']}x{img['height']}, {img['source']})")

    # 3. 去重：移除与内嵌图片重叠的裁剪区域
    print("\n[步骤 3/3] 去重...")
    all_images = remove_duplicate_extractions(embedded, cropped, iou_threshold=0.6)
    print(f"[结果] 去重后共 {len(all_images)} 张唯一裁剪图片")

    # 4. 渲染页面（可选）
    rendered = []
    if args.render_pages:
        print("\n[可选] 渲染页面为高清图片...")
        rendered = render_pages_as_fallback(doc, output_dir / "pages", dpi=args.dpi)
        print(f"[结果] 渲染了 {len(rendered)} 页")

    summary = {
        "pdf_path": str(pdf_path),
        "output_dir": str(output_dir),
        "total_pages": len(doc),
        "embedded_images_scanned": len(embedded),
        "cropped_regions": len(cropped),
        "unique_crop_images": len(all_images),
        "rendered_pages": len(rendered),
        "images": all_images,
    }
    summary_path = output_dir / "extraction_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    doc.close()

    print("\n" + "=" * 50)
    print(f"[完成] 全部提取完毕! 共保留 {len(all_images)} 张唯一裁剪图片。")
    print(f"[汇总] 结果保存在: {output_dir}")


if __name__ == "__main__":
    main()
