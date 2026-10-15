"""Memori jangka panjang It's Me.

Sederhana saja: satu berkas Markdown. Setiap catatan ditambahkan ke bawah
beserta tanggalnya. Isi berkas ini disisipkan ke system prompt tiap giliran,
jadi It's Me selalu ingat hal yang pernah kamu beri tahu.
"""

import os
from datetime import datetime


class Memory:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("# Memori It's Me\n\n")

    def text(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""

    def add(self, note):
        note = (note or "").strip()
        if not note:
            return "Catatan kosong, tidak disimpan."
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"- ({stamp}) {note}\n")
        return "Tersimpan di memori."
