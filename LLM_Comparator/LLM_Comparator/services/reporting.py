import csv
import io
from typing import Iterable, List, Tuple


def recall_at(rank: int, cutoff: int) -> str:
    return "Yes" if rank != -1 and rank <= cutoff else "No"


def write_csv(path: str, header: List[str], rows: Iterable[List[str]]) -> Tuple[str, bytes]:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return path, output.getvalue().encode("utf-8")





