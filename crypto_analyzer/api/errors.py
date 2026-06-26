class TooManyRecordsError(Exception):
    """在 API 分頁抓取過程中，累計筆數超過上限時拋出。"""
    def __init__(self, count: int):
        self.count = count
        super().__init__(f"查詢已停止：已抓取 {count:,} 筆，超過上限")
