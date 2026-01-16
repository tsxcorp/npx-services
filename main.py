from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import qrcode
import io
import base64
import hashlib
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="QR Code Generator API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.nexpo.vn", "http://app.nexpo.vn", "https://cms.nexpo.vn", "https://api-stillgood.tsx.vn"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize AWS SES client
ses_client = boto3.client(
    'ses',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION', 'us-east-1')
)

class QRCodeRequest(BaseModel):
    text: str

class QRCodeResponse(BaseModel):
    qr_code_base64: str
    file_name: str
    success: bool
    message: str

class EmailRequest(BaseModel):
    from_email: str
    to: str
    subject: str
    html: str
    content_qr: str

class EmailResponse(BaseModel):
    success: bool
    message: str
    message_id: str = None

@app.get("/")
async def root():
    return {"message": "QR Code Generator API is running!"}

@app.post("/gen-qr", response_model=QRCodeResponse)
async def generate_qr_code(request: QRCodeRequest):
    """
    Tạo QR code từ string và trả về base64
    """
    try:
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="Text không được để trống")
        
        # Tạo QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(request.text)
        qr.make(fit=True)
        
        # Tạo image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Chuyển đổi thành base64
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        # Tạo tên file từ hash của nội dung và timestamp
        text_hash = hashlib.md5(request.text.encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"qr_{text_hash}_{timestamp}.png"
        
        return QRCodeResponse(
            qr_code_base64=img_base64,
            file_name=file_name,
            success=True,
            message="QR code được tạo thành công"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi tạo QR code: {str(e)}")

@app.get("/download-qr")
async def download_qr_code(text: str = Query(..., description="Nội dung mã QR để tải về")):
    """
    Tạo QR code từ string và trả về file ảnh để tải về trực tiếp
    """
    try:
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text không được để trống")
        
        # Tạo QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)
        
        # Tạo image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Lưu vào buffer
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Tạo tên file từ hash của nội dung và timestamp
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"qr_{text_hash}_{timestamp}.png"
        
        return StreamingResponse(
            buffer,
            media_type="image/png",
            headers={
                "Content-Disposition": f"attachment; filename={file_name}"
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý tải QR code: {str(e)}")

def generate_qr_code_base64(content_qr: str) -> str:
    """
    Tạo QR code từ content_qr và trả về base64 string
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(content_qr)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return img_base64

def append_qr_to_html(html: str, qr_base64: str) -> str:
    """
    Gắn QR code image vào dưới cùng của HTML
    """
    qr_img_tag = f'<div style="text-align: center; margin-top: 20px;"><img src="data:image/png;base64,{qr_base64}" alt="QR Code" /></div>'
    
    # Tìm vị trí đóng body tag và chèn QR code trước đó
    if '</body>' in html:
        html = html.replace('</body>', f'{qr_img_tag}</body>')
    elif '</html>' in html:
        html = html.replace('</html>', f'{qr_img_tag}</html>')
    else:
        # Nếu không có body tag, append vào cuối
        html = html + qr_img_tag
    
    return html

@app.post("/send-email-with-qr", response_model=EmailResponse)
async def send_email_with_qr(request: EmailRequest):
    """
    Nhận thông tin email, tạo QR code từ content_qr, gắn vào HTML và gửi email qua AWS SES
    """
    try:
        # Validate input
        if not request.from_email.strip():
            raise HTTPException(status_code=400, detail="from_email không được để trống")
        if not request.to.strip():
            raise HTTPException(status_code=400, detail="to không được để trống")
        if not request.subject.strip():
            raise HTTPException(status_code=400, detail="subject không được để trống")
        if not request.content_qr.strip():
            raise HTTPException(status_code=400, detail="content_qr không được để trống")
        
        # Tạo QR code và chuyển sang base64
        qr_base64 = generate_qr_code_base64(request.content_qr)
        
        # Gắn QR code vào HTML
        html_with_qr = append_qr_to_html(request.html, qr_base64)
        
        # Gửi email qua AWS SES
        try:
            response = ses_client.send_email(
                Source=request.from_email,
                Destination={
                    'ToAddresses': [request.to]
                },
                Message={
                    'Subject': {
                        'Data': request.subject,
                        'Charset': 'UTF-8'
                    },
                    'Body': {
                        'Html': {
                            'Data': html_with_qr,
                            'Charset': 'UTF-8'
                        }
                    }
                }
            )
            
            return EmailResponse(
                success=True,
                message="Email đã được gửi thành công",
                message_id=response['MessageId']
            )
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            raise HTTPException(
                status_code=500,
                detail=f"Lỗi khi gửi email qua AWS SES: {error_code} - {error_message}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
