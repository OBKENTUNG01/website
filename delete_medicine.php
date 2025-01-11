<?php
session_start();

if (!isset($_SESSION["loggedin"]) || $_SESSION["loggedin"] !== true) {
    header("Location: login.php");
    exit();
}

require 'config.php';

if (isset($_GET["id"])) {
    $id = $_GET["id"];
    
    // Hapus riwayat perubahan terlebih dahulu
    $sql_riwayat = "DELETE FROM riwayat WHERE obat_id=$id";
    if ($conn->query($sql_riwayat) === TRUE) {
        // Hapus data obat setelah menghapus riwayat perubahan
        $sql_obat = "DELETE FROM obat WHERE id=$id";
        if ($conn->query($sql_obat) === TRUE) {
            header("Location: obat.php");
            exit();
        } else {
            echo "Error: " . $sql_obat . "<br>" . $conn->error;
        }
    } else {
        echo "Error: " . $sql_riwayat . "<br>" . $conn->error;
    }
} else {
    echo "ID tidak diberikan";
}
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hapus Obat - Apotek Modern</title>
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
        <h1>Hapus Obat</h1>
        <p>Obat berhasil dihapus.</p>
        <a href="obat.php">Kembali ke Daftar Obat</a>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
