<?php
$servername = "localhost";
$username = "root";
$password = "";
$dbname = "apotek";

// Buat koneksi
$conn = new mysqli($servername, $username, $password, $dbname);

// Cek koneksi
if ($conn->connect_error) {
    die("Connection failed: " . $conn->connect_error);
}

function checkRole($required_role) {
    return isset($_SESSION["role"]) && $_SESSION["role"] === $required_role;
}

function checkRoles($required_roles) {
    return isset($_SESSION["role"]) && in_array($_SESSION["role"], $required_roles);
}
?>
