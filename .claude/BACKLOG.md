# nexpo-services — Backlog

> Không commit lên git. Claude tự cập nhật cuối mỗi session.
> Cross-project decisions: xem `nexpo-platform/.claude/PROGRESS.md`

---

## ✅ Đã làm xong

### [2026-03] CORS fix
- Thêm `portal.nexpo.vn`, `insights.nexpo.vn`, ports 3002/3003 vào allow_origins

### [2026-03] AI Matching — Token optimization

**`POST /match/run` thay đổi:**
- `KEYWORD_THRESHOLD`: `0.05` → `0.15` — loại bỏ nhiều cặp không liên quan trước khi gọi AI
- `MAX_CANDIDATES_PER_JOB = 40` — sau keyword filter, sort by keyword score desc, chỉ lấy top-40 vào AI
- Kết quả: từ ~362 AI calls/job → tối đa 40 calls/job (giảm ~90%)
- Response message giờ hiển thị stats: `Checked N jobs × top-40 of 362 candidates (keyword threshold 15%)`

**⚠️ Prod chưa deploy** — prod cũ (không có threshold) tạo suggestions dưới 50%, cần cleanup thủ công (xem bên dưới)

**Data cleanup event 30 (2026-03-24):**
- Xóa 790 suggestions dưới 50% + duplicates (lần 1)
- Xóa thêm 140 suggestions sau khi prod cũ chạy lại (lần 2 + 3 per-exhibitor)
- State sạch: 269 pending ≥50%, 1 converted_to_meeting, 0 approved/rejected bị đụng

---

## 🔄 In Progress / Chưa xong

_(không có task đang dở)_

---

## 📋 Backlog

- [ ] Refactor `main.py` thành router modules (`routers/qr.py`, `routers/email.py`, v.v.)
- [ ] `POST /send-email-with-qr`: thêm param `link_type: "registration"|"ticket"` — ảnh hưởng URL trong `inject_qr_extras()`
- [ ] Thêm `POST /send-email` — gửi email không có QR (dùng cho payment failed, order expired)
- [ ] APScheduler: expire pending ticket orders mỗi 5 phút (cần `apscheduler==3.10.4`)
- [ ] `POST /generate-email-template`: thêm case `form_purpose = "ticket_confirmation"`
