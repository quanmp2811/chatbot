from app.services.file_parser import extract_text

print("PDF:", len(extract_text("test.pdf")))
print("EXCEL:", len(extract_text("test.xlsx")))
print("IMAGE:", len(extract_text("test.png")))