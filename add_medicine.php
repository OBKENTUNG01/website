<?php
session_start();

if (!isset($_SESSION["loggedin"]) || $_SESSION["loggedin"] !== true) {
    header("Location: login.php");
    exit();
}

require 'config.php';

if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $nama = $_POST["nama"];
    $jumlah_masuk = $_POST["jumlah_masuk"];
    $jumlah_keluar = $_POST["jumlah_keluar"];
    $tanggal_masuk = $_POST["tanggal_masuk"];
    $tanggal_keluar = $_POST["tanggal_keluar"];
    $tanggal_kadaluarsa = $_POST["tanggal_kadaluarsa"];

    $sql = "INSERT INTO obat (nama, jumlah_masuk, jumlah_keluar, tanggal_masuk, tanggal_keluar, tanggal_kadaluarsa)
            VALUES ('$nama', $jumlah_masuk, $jumlah_keluar, '$tanggal_masuk', '$tanggal_keluar', '$tanggal_kadaluarsa')";

    if ($conn->query($sql) === TRUE) {
        header("Location: obat.php");
        exit();
    } else {
        echo "Error: " . $sql . "<br>" . $conn->error;
    }
}
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tambah Obat - Apotek Modern</title>
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
        <h1>Tambah Obat</h1>
        <form method="post" action="">
            <label>Nama:</label>
            <input type="text" name="nama" required>
            <label>Jumlah Masuk:</label>
            <input type="number" name="jumlah_masuk" required>
            <label>Jumlah Keluar:</label>
            <input type="number" name="jumlah_keluar" required>
            <label>Tanggal Masuk:</label>
            <input type="date" name="tanggal_masuk" required>
            <label>Tanggal Keluar:</label>
            <input type="date" name="tanggal_keluar" required>
            <label>Tanggal Kadaluarsa:</label>
            <input type="date" name="tanggal_kadaluarsa" required>
            <input type="submit" value="Tambah">
        </form>
        <a href="obat.php">Kembali ke Daftar Obat</a>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
