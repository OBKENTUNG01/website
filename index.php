<?php
session_start();
require 'config.php';
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Beranda - Apotek Modern</title>
    <link rel="stylesheet" type="text/css" href="style.css">
</head>
<body>
    <header>
        <nav>
            <ul>
                <li><a href="index.php">Beranda</a></li>
                <li><a href="obat.php">Obat</a></li>
                <li><a href="info.php">Info Apotek</a></li>
                <?php if (isset($_SESSION["loggedin"]) && $_SESSION["loggedin"] === true): ?>
                    <li><a href="logout.php">Logout</a></li>
                <?php else: ?>
                    <li><a href="login.php">Login</a></li>
                    <li><a href="register.php">Register</a></li>
                <?php endif; ?>
            </ul>
        </nav>
    </header>
    <main>
        <h1>Selamat Datang di Apotek Modern</h1>
        <p>Kami menyediakan berbagai macam obat dan layanan kesehatan terbaik untuk Anda. Telusuri koleksi obat kami di halaman <a href="obat.php">Daftar Obat</a>.</p>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
