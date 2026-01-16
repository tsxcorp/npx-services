# QR Code Generator API

Một FastAPI application để tạo QR code từ string và trả về base64.

## Cài đặt

1. Cài đặt dependencies:
```bash
pip install -r requirements.txt
```

2. Chạy ứng dụng:
```bash
python main.py
```

Hoặc sử dụng uvicorn:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Sử dụng

### API Endpoint

**POST** `/gen-qr`

Tạo QR code từ string và trả về base64.

#### Request Body:
```json
{
  "text": "Nội dung muốn tạo QR code"
}
```

#### Response:
```json
{
  "qr_code_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
  "success": true,
  "message": "QR code được tạo thành công"
}
```

### Ví dụ sử dụng với curl:

```bash
curl -X POST "http://localhost:8000/gen-qr" \
     -H "Content-Type: application/json" \
     -d '{"text": "Hello World!"}'
```

### Ví dụ sử dụng với Python:

```python
import requests
import base64
from PIL import Image
import io

response = requests.post("http://localhost:8000/gen-qr", 
                        json={"text": "Hello World!"})
data = response.json()

if data["success"]:
    # Decode base64 và hiển thị image
    img_data = base64.b64decode(data["qr_code_base64"])
    img = Image.open(io.BytesIO(img_data))
    img.show()
```

## API Documentation

Sau khi chạy ứng dụng, truy cập:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Cấu trúc dự án

```
nexpo-service/
├── main.py              # FastAPI application
├── requirements.txt     # Dependencies
└── README.md           # Documentation
```
# npx-services
