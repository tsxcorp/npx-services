import hashlib
from datetime import datetime
from fastapi import APIRouter, HTTPException
from app.models.schemas import QRCodeRequest, QRCodeResponse
from app.services.qr_service import generate_qr_code_bytes
import base64
import qrcode
import io

router = APIRouter()


@router.get("/")
async def root():
    return {"message": "QR Code Generator API is running!"}


@router.post("/gen-qr", response_model=QRCodeResponse)
async def generate_qr_code(request: QRCodeRequest):
    """Generate QR code from string and return as base64 PNG."""
    try:
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="Text không được để trống")

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(request.text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode()

        text_hash = hashlib.md5(request.text.encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"qr_{text_hash}_{timestamp}.png"

        return QRCodeResponse(
            qr_code_base64=img_base64,
            file_name=file_name,
            success=True,
            message="QR code được tạo thành công",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi tạo QR code: {str(e)}")
