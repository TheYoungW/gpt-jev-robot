"""One source for metric ChArUco detection geometry and vector A4 printing."""
from pathlib import Path
import hashlib
import json
import cv2
import numpy as np
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from .models import BoardSpec, CalibrationError, fingerprint


def make_board(spec: BoardSpec):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board = cv2.aruco.CharucoBoard((spec.squares_x, spec.squares_y), spec.square_m, spec.marker_m, dictionary)
    board.setLegacyPattern(False)
    return board


def write_board(directory, spec=None):
    spec = spec or BoardSpec()
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise CalibrationError('Board output must be a new or empty directory')
    width, height = spec.squares_x*spec.square_m*1000, spec.squares_y*spec.square_m*1000
    if width > 180 or height > 250:
        raise CalibrationError('Board will not fit the A4 print margins; choose smaller squares')
    directory.mkdir(parents=True, exist_ok=True)
    board = make_board(spec)
    # 100 pixels per square; default marker 72px = exactly 6 modules of 12px.
    pattern = board.generateImage((spec.squares_x*100, spec.squares_y*100), marginSize=0, borderBits=1)
    pdf = directory/'d405_charuco_A4.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(210*mm, 297*mm), pageCompression=1, invariant=1)
    c.setTitle('D405 ChArUco A4 - print actual size 100 percent')
    c.setAuthor('gpt-jev-robot')
    c.setFont('Helvetica-Bold', 10)
    c.drawString(17.5*mm, 287*mm, 'D405 HAND-EYE CALIBRATION / ChArUco')
    c.setFont('Helvetica', 7)
    c.drawString(17.5*mm, 282*mm, f'{spec.squares_x} x {spec.squares_y} squares | square {spec.square_m*1000:.2f} mm | marker {spec.marker_m*1000:.2f} mm | 4X4_50')
    left, top = (210-width)/2, 20.
    scale = spec.square_m*1000/100
    c.setFillColorRGB(0, 0, 0)
    # Preserve exact board geometry as filled vector rectangles, no image scaling.
    for y, row in enumerate(pattern):
        edges = np.diff(np.r_[False, row == 0, False].astype(int))
        for begin, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            c.rect((left+begin*scale)*mm, (297-top-(y+1)*scale)*mm,
                   (end-begin)*scale*mm, scale*mm, stroke=0, fill=1)
    c.setLineWidth(.2*mm)
    c.line(17.5*mm, 16*mm, 117.5*mm, 16*mm)
    for x in (17.5, 117.5):c.line(x*mm, 14*mm, x*mm, 18*mm)
    c.setFont('Helvetica', 7)
    c.drawString(42*mm, 11*mm, '100.00 mm - verify with a ruler')
    c.line(201*mm, 85*mm, 201*mm, 185*mm)
    for y in (85, 185):c.line(199*mm, y*mm, 203*mm, y*mm)
    c.saveState();c.translate(205*mm, 108*mm);c.rotate(90)
    c.drawString(0, 0, '100.00 mm vertical check');c.restoreState()
    c.setFont('Helvetica-Bold', 7)
    c.drawString(17.5*mm, 5.5*mm, 'A4 / ACTUAL SIZE 100% / NO FIT TO PAGE / MOUNT FLAT')
    c.showPage();c.save()
    # PNG is a detector fixture/preview only; the PDF is the print master.
    cv2.imwrite(str(directory/'board_pattern.png'), np.pad(pattern, 40, constant_values=255))
    (directory/'board.json').write_text(spec.model_dump_json(indent=2))
    manifest={'board_fingerprint':fingerprint(spec),'opencv_version':cv2.__version__,
              'board_size_mm':[width,height],'page_size_mm':[210,297],
              'marker_ids':board.getIds().reshape(-1).tolist(),
              'corner_points_board_m':board.getChessboardCorners().tolist(),
              'pattern_sha256':hashlib.sha256(pattern.tobytes()).hexdigest(),
              'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
              'coordinates':'OpenCV board.getChessboardCorners coordinates, meters; T_camera_board from PnP. '
                            'The printed page margin is not the board origin.'}
    (directory/'board_manifest.json').write_text(json.dumps(manifest, indent=2))
    return pdf
