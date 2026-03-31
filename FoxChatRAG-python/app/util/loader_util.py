import mimetypes

from langchain_community.document_loaders import UnstructuredWordDocumentLoader, TextLoader, CSVLoader, PyPDFLoader
from langchain_core.documents import Document

from app.common import FileTypeConstant


async def docx_loader(file_path: str):
    loader = UnstructuredWordDocumentLoader(
        file_path,
    )

    documents = loader.load()

    return documents

async def txt_loader(file_path: str):
    loader = TextLoader(
        file_path,
        encoding="utf-8",
    )

    documents = loader.load()

    return documents

async def csv_loader(file_path: str):
    loader = CSVLoader(
        file_path,
        encoding="utf-8",
    )

    documents = loader.load()

    return documents

async def pdf_loader(file_path: str):
    loader = PyPDFLoader(
        file_path,
    )

    documents = loader.load()

    return documents

async def str_loader(content: str):
    documents:list = [Document(page_content=content)]

    return documents

# 文件类型对应文件加载器
LOADER_MAP = {
    FileTypeConstant.CSV: csv_loader,
    FileTypeConstant.TXT: txt_loader,
    FileTypeConstant.DOCX: docx_loader,
    FileTypeConstant.PDF: pdf_loader,
    FileTypeConstant.MD: txt_loader,
    FileTypeConstant.STR: str_loader,
}

async def load_file(local_file_path: str, file_type: FileTypeConstant):
    loader = LOADER_MAP[file_type]

    documents = await loader(local_file_path)

    return documents