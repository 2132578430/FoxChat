from langchain_text_splitters import RecursiveCharacterTextSplitter

documents_splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", ".", " ", "!", "，", "。", "！", ""],    # 需要换段落的分隔符号
        chunk_size=1000,     # 分段的最大字符数
        chunk_overlap=100,       # 分段之后允许的最大重叠字符数（为了保证上下文连贯）
        length_function=len,        # 统计字符的依赖函数
    )