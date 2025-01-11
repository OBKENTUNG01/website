<?php
session_start();

if (!isset($_SESSION["loggedin"]) || $_SESSION["loggedin"] !== true) {
    header("Location: login.php");
    exit();
}
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Info Apotek - Apotek Modern</title>
    <link rel="stylesheet" type="text/css" href="style.css">
</head>
<body>
    <header>
        <nav>
            <ul>
                <li><a href="index.php">Beranda</a></li>
                <li><a href="obat.php">Obat</a></li>
                <li><a href="info.php">Info Apotek</a></li>
                <li><a href="logout.php">Logout</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <h1>Info Apotek</h1>
        <section>
            <h2>Tentang Kami</h2>
            <p>Apotek Modern berdiri sejak berdirinya rutan samarinda dan berkomitmen untuk menyediakan layanan kesehatan terbaik untuk Anda. Kami memiliki berbagai macam obat dan produk kesehatan lainnya yang siap memenuhi kebutuhan Anda.</p>
        </section>
        <section>
            <h2>Layanan Kami</h2>
            <ul>
                <li>Pembelian Obat Resep dan Non-Resep</li>
                <li>Konsultasi dengan Dokter atau Perawat</li>
                <li>Pengukuran Tekanan Darah dan Gula Darah</li>
                <li>Pengelolaan Resep Kronis</li>
            </ul>
        </section>
        <section>
            <h2>Jam Operasional</h2>
            <p>Senin - Jumat: 08.00 - 20.00</p>
            <p>Sabtu: 09.00 - 17.00</p>
            <p>Minggu: Tutup</p>
        </section>
        <section>
            <h2>Kontak Kami</h2>
            <p>
                <a href="https://www.instagram.com/rutan.samarinda/" target="_blank">
                    <img src="ig.png" alt="Instagram Logo" width="50" height="50">
                </a>
            </p>
        </section>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
