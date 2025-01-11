<?php
$servername = "localhost";
$username = "root";
$password = "";
$dbname = "apotek";

$conn = new mysqli($servername, $username, $password, $dbname);

if ($_SERVER["REQUEST_METHOD"] == "POST") {
    $id = $_POST["id"];
    $nama = $_POST["nama"];
    $stok = $_POST["stok"];
    $tanggal_kadaluarsa = $_POST["tanggal_kadaluarsa"];
    $harga = $_POST["harga"];
    $produsen = $_POST["produsen"];

    // Pastikan nilai harga adalah numerik
    $harga = floatval($harga);

    $sql = "UPDATE obat SET nama='$nama', stok=$stok, tanggal_kadaluarsa='$tanggal_kadaluarsa', harga=$harga, produsen='$produsen' WHERE id=$id";

    if ($conn->query($sql) === TRUE) {
        header("Location: obat.php");
        exit();
    } else {
        echo "Error: " . $sql . "<br>" . $conn->error;
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
    <title>Edit Obat - Apotek Modern</title>
    <link rel="stylesheet" type="text/css" href="style.css">
</head>
<body>
    <header>
        <nav>
            <ul>
                <li><a href="index.php">Beranda</a></li>
                <li><a href="obat.php">Obat</a></li>
                <li><a href="info.php">Info Apotek</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <h1>Edit Obat</h1>
        <form method="post" action="">
            <input type="hidden" name="id" value="<?= $row['id'] ?>">
            <label>Nama:</label>
            <input type="text" name="nama" value="<?= $row['nama'] ?>" required>
            <label>Stok:</label>
            <input type="number" name="stok" value="<?= $row['stok'] ?>" required>
            <label>Tanggal Kadaluarsa:</label>
            <input type="date" name="tanggal_kadaluarsa" value="<?= $row['tanggal_kadaluarsa'] ?>" required>
            <label>Harga:</label>
            <input type="text" name="harga" value="<?= $row['harga'] ?>" required>
            <label>Produsen:</label>
            <input type="text" name="produsen" value="<?= $row['produsen'] ?>" required>
            <input type="submit" value="Simpan">
        </form>
        <a href="obat.php">Kembali ke Daftar Obat</a>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
