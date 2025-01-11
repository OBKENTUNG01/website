<?php
session_start();

if (!isset($_SESSION["loggedin"]) || $_SESSION["loggedin"] !== true) {
    header("Location: login.php");
    exit();
}

require 'config.php';

if (isset($_GET["id"])) {
    $id = $_GET["id"];
    $sql = "SELECT * FROM obat WHERE id=$id";
    $result = $conn->query($sql);

    if ($result->num_rows > 0) {
        $row = $result->fetch_assoc();
    } else {
        echo "Tidak ada obat dengan ID tersebut";
        exit();
    }
} else {
    echo "ID tidak diberikan";
    exit();
}

// Mendapatkan perubahan terbaru dari riwayat
$sql_riwayat = "SELECT * FROM riwayat WHERE obat_id=$id ORDER BY tanggal DESC LIMIT 1";
$result_riwayat = $conn->query($sql_riwayat);
$riwayat = $result_riwayat->fetch_assoc();
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Perubahan Stok Obat - Apotek Modern</title>
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
        <h1>Perubahan Stok Obat</h1>
        <p><strong>Nama Obat:</strong> <?= $row['nama'] ?></p>
        <?php if ($riwayat): ?>
            <?php if ($riwayat['jumlah_masuk'] != 0): ?>
                <p><strong>Jumlah Masuk:</strong> <?= $riwayat['jumlah_masuk'] ?></p>
            <?php endif; ?>
            <?php if ($riwayat['jumlah_keluar'] != 0): ?>
                <p><strong>Jumlah Keluar:</strong> <?= $riwayat['jumlah_keluar'] ?></p>
            <?php endif; ?>
            <?php if ($riwayat['tanggal_masuk'] != $row['tanggal_masuk']): ?>
                <p><strong>Tanggal Masuk:</strong> <?= $riwayat['tanggal_masuk'] ?></p>
            <?php endif; ?>
            <?php if ($riwayat['tanggal_keluar'] != $row['tanggal_keluar']): ?>
                <p><strong>Tanggal Keluar:</strong> <?= $riwayat['tanggal_keluar'] ?></p>
            <?php endif; ?>
        <?php else: ?>
            <p>Tidak ada perubahan yang tercatat.</p>
        <?php endif; ?>
        <a href="obat.php">Kembali ke Daftar Obat</a>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
