<?php
session_start();
require 'config.php';

$sql = "SELECT * FROM obat";
$result = $conn->query($sql);
?>
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daftar Obat - Apotek Modern</title>
    <link rel="stylesheet" href="style.css">
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
                <?php endif; ?>
            </ul>
        </nav>
    </header>
    <main>
        <h1>Daftar Obat</h1>
        <?php if (checkRole('admin')): ?>
            <a href="add_medicine.php">Tambah Obat</a>
        <?php endif; ?>
        <table>
            <thead>
                <tr>
                    <th>Nama</th>
                    <?php if (isset($_SESSION["loggedin"]) && $_SESSION["loggedin"] === true): ?>
                        <th>Jumlah Masuk</th>
                        <th>Jumlah Keluar</th>
                        <th>Tanggal Masuk</th>
                        <th>Tanggal Keluar</th>
                        <th>Sisa Obat</th>
                    <?php endif; ?>
                    <?php if (checkRole('admin')): ?>
                        <th>Aksi</th>
                    <?php endif; ?>
                </tr>
            </thead>
            <tbody>
                <?php if ($result->num_rows > 0): ?>
                    <?php while($row = $result->fetch_assoc()): ?>
                        <tr>
                            <td><?= htmlspecialchars($row["nama"]) ?></td>
                            <?php if (isset($_SESSION["loggedin"]) && $_SESSION["loggedin"] === true): ?>
                                <td><?= htmlspecialchars($row["jumlah_masuk"]) ?></td>
                                <td><?= htmlspecialchars($row["jumlah_keluar"]) ?></td>
                                <td><?= htmlspecialchars($row["tanggal_masuk"]) ?></td>
                                <td><?= htmlspecialchars($row["tanggal_keluar"]) ?></td>
                                <td><?= htmlspecialchars($row["jumlah_masuk"] - $row["jumlah_keluar"]) ?></td>
                            <?php endif; ?>
                            <?php if (checkRole('admin')): ?>
                                <td>
                                    <a href="update_stock.php?id=<?= $row['id'] ?>">Update Stok</a>
                                    <a href="delete_medicine.php?id=<?= $row['id'] ?>" onclick="return confirm('Apakah Anda yakin ingin menghapus obat ini?')">Hapus</a>
                                </td>
                            <?php endif; ?>
                        </tr>
                        <?php if (checkRoles(['admin', 'user'])): ?>
                            <tr>
                                <td colspan="7">
                                    <strong>Riwayat Perubahan:</strong>
                                    <ul>
                                        <?php
                                        $riwayat_sql = "SELECT * FROM riwayat WHERE obat_id=" . $row['id'] . " ORDER BY tanggal DESC";
                                        $riwayat_result = $conn->query($riwayat_sql);
                                        if ($riwayat_result->num_rows > 0) {
                                            while($riwayat_row = $riwayat_result->fetch_assoc()) {
                                                echo "<li>";
                                                if ($riwayat_row["jumlah_masuk"] != 0) {
                                                    echo "Jumlah Masuk: " . htmlspecialchars($riwayat_row["jumlah_masuk"]);
                                                }
                                                if ($riwayat_row["jumlah_keluar"] != 0) {
                                                    echo " Jumlah Keluar: " . htmlspecialchars($riwayat_row["jumlah_keluar"]);
                                                }
                                                if (!empty($riwayat_row["tanggal_masuk"])) {
                                                    echo " Tanggal Masuk: " . htmlspecialchars($riwayat_row["tanggal_masuk"]);
                                                }
                                                if (!empty($riwayat_row["tanggal_keluar"])) {
                                                    echo " Tanggal Keluar: " . htmlspecialchars($riwayat_row["tanggal_keluar"]);
                                                }
                                                echo "</li>";
                                            }
                                        } else {
                                            echo "<li>Tidak ada riwayat perubahan</li>";
                                        }
                                        ?>
                                    </ul>
                                </td>
                            </tr>
                        <?php endif; ?>
                    <?php endwhile; ?>
                <?php else: ?>
                    <tr>
                        <td colspan="7">Tidak ada obat</td>
                    </tr>
                <?php endif; ?>
            </tbody>
        </table>
    </main>
    <footer>
        <p>&copy; 2025 Apotek Modern</p>
    </footer>
</body>
</html>
<?php $conn->close(); ?>
