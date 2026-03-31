import fitz # PyMuPDF
doc = fitz.open("T&A ÁO DÀI CARA 2026 - PO 2 ÁO DÀI.pdf")
print("Pages:", len(doc))
for page in doc:
    print(f"Page {page.number} has {len(page.widgets())} widgets (form fields)")
    for w in page.widgets():
        print(w.field_name, w.field_type_string)
