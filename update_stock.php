<?php
session_start();

if (!isset($_SESSION["loggedin"]) || $_SESSION["loggedin"] !== true) {
    header("Location: login.php");
    exit();
}

require 'config.php';

if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $id = $_POST["id"];
    $jumlah_masuk = $_POST["jumlah_masuk"];
    $jumlah_keluar = $_POST["jumlah_keluar"];
    $tanggal_masuk = $_POST["tanggal_masuk"];
    $tanggal_keluar = $_POST["tanggal_keluar"];
    $perubahan_stok = $jumlah_masuk - $jumlah_keluar;

    // Array untuk menyimpan perubahan yang terjadi
    $perubahan = array();

    // Periksa dan simpan perubahan jumlah masuk
    if ($jumlah_masuk != 0) {
        $perubahan[] = "jumlah_masuk = jumlah_masuk + $jumlah_masuk";
    }

    // Periksa dan simpan perubahan jumlah keluar
    if ($jumlah_keluar != 0) {
        $perubahan[] = "jumlah_keluar = jumlah_keluar + $jumlah_keluar";
    }

    // Periksa dan simpan perubahan tanggal masuk
    if (!empty($tanggal_masuk)) {
        $perubahan[] = "tanggal_masuk = '$tanggal_masuk'";
    }

    // Periksa dan simpan perubahan tanggal keluar
    if (!empty($tanggal_keluar)) {
        $perubahan[] = "tanggal_keluar = '$tanggal_keluar'";
    }

    // Bangun query update jika ada perubahan
    if (!empty($perubahan)) {
        $sql = "UPDATE obat SET " . implode(", ", $perubahan) . " WHERE id = $id";

        if ($conn->query($sql) === TRUE) {
            $sql_riwayat = "INSERT INTO riwayat (obat_id, perubahan_stok, tanggal_masuk, tanggal_keluar, jumlah_masuk, jumlah_keluar) 
                            VALUES ($id, $perubahan_stok, '$tanggal_masuk', '$tanggal_keluar', $jumlah_masuk, $jumlah_keluar)";
            $conn->query($sql_riwayat);
            header("Location: obat.php");
            exit();
        } else {
            echo "Error: " . $sql . "<br>" . $conn->error;
        }
    } else {
        header("Location: obat.php");
        exit();
    }
} elseif (isset($_GET["id"])) {
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
?>
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Update Stok Obat - Apotek Modern</title>
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
        <h1>Update Stok Obat</h1>
        <form method="post" action="">
            <input type="hidden" name="id" value="<?= $row['id'] ?>">
            <label>Jumlah Masuk:</label>
            <input type="number" name="jumlah_masuk" value="0">
            <label>Jumlah Keluar:</label>
            <input type="number" name="jumlah_keluar" value="0">
            <label>Tanggal Masuk:</label>
            <input type="date" name="tanggal_masuk" value="<?= $row['tanggal_masuk'] ?>">
            <label>Tanggal Keluar:</label>
            <input type="date" name="tanggal_keluar" value="<?= $row['tanggal_keluar'] ?>">
            <input type="submit" value="Update">
        </form>
        <a href="obat.php">Kembali ke Daftar Obat</a>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
