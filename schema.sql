-- Smart Queue Clinic database schema
-- Run this once via:  mysql -u root -p < schema.sql
-- (app.py also auto-creates the database and tables on first run)

CREATE DATABASE IF NOT EXISTS smart_queue_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_queue_db;

CREATE TABLE IF NOT EXISTS patients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    age INT NOT NULL,
    email VARCHAR(120) NOT NULL UNIQUE,
    mobile VARCHAR(15) NOT NULL,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS doctors (
    id INT AUTO_INCREMENT PRIMARY KEY,
    doctor_name VARCHAR(100) NOT NULL,
    username VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    gpay_qr LONGTEXT DEFAULT NULL,
    email VARCHAR(120) DEFAULT NULL,
    mobile VARCHAR(15) DEFAULT NULL,
    specialization VARCHAR(100) DEFAULT NULL,
    fees DECIMAL(10,2) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS appointments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT DEFAULT NULL,
    patient_name VARCHAR(100) NOT NULL,
    patient_age INT NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time VARCHAR(20) NOT NULL,
    doctor_id INT NOT NULL,
    doctor_name VARCHAR(100) NOT NULL,
    payment_mode VARCHAR(10) NOT NULL DEFAULT 'cash',
    payment_status VARCHAR(10) NOT NULL DEFAULT 'cash',
    paid_at DATETIME DEFAULT NULL,
    amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    token_number INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'waiting',
    postponed_date DATE DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_doctor_date (doctor_id, appointment_date),
    INDEX idx_patient (patient_id),
    CONSTRAINT fk_appt_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE SET NULL,
    CONSTRAINT fk_appt_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS reminders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT NOT NULL,
    appointment_id INT DEFAULT NULL,
    doctor_name VARCHAR(100) DEFAULT NULL,
    medicine_name VARCHAR(255) NOT NULL,
    dosage VARCHAR(100) NOT NULL,
    frequency VARCHAR(50) NOT NULL,
    food_timing VARCHAR(20) NOT NULL DEFAULT 'after food',
    start_date DATE NOT NULL,
    duration_days INT NOT NULL DEFAULT 1,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_reminder_patient (patient_id),
    CONSTRAINT fk_rem_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    CONSTRAINT fk_rem_appt FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS queue_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    doctor_id INT NOT NULL,
    queue_date DATE NOT NULL,
    started_at DATETIME DEFAULT NULL,
    ended_at DATETIME DEFAULT NULL,
    UNIQUE KEY uq_doctor_date (doctor_id, queue_date)
) ENGINE=InnoDB;
