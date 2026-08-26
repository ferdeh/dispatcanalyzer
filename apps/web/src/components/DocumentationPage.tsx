import {
  ArrowUp,
  BookOpen,
  Calculator,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ExternalLink,
  Lightbulb,
  ListTree,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { AppPage } from "./AppSidebar";

type CardRow = { name: string; meaning: string; reading: string };
type Example = { title: string; text: string };
type Step = { title: string; text: string };
type GuideTopic = {
  id: string;
  title: string;
  paragraphs?: string[];
  steps?: Step[];
  cards?: CardRow[];
  formulas?: string[];
  examples?: Example[];
  note?: string;
};
type GuidePage = {
  id: string;
  number: string;
  title: string;
  description: string;
  page?: AppPage;
  topics: GuideTopic[];
};

const guides: GuidePage[] = [
  {
    id: "doc-mulai",
    number: "01",
    title: "Mulai Menggunakan",
    description: "Tujuan, urutan kerja, dan konvensi yang berlaku di seluruh aplikasi.",
    topics: [
      {
        id: "doc-tujuan",
        title: "Tujuan dan batas aplikasi",
        paragraphs: [
          "Dispatch Intelligence Platform mengubah Master Data, Loading Order, GPS, dan histori dispatch menjadi informasi operasional yang dapat diaudit. Phase 0–5 terutama menjelaskan pola historis; Phase 6 menghasilkan prediction/assignment berbasis snapshot model; Phase 7 menghasilkan route plan multi-trip dan jadwal depot bay yang versioned.",
          "Phase 7 memakai prediction Phase 6 sebagai warm start dan soft preference, bukan hard assignment. Probability, affinity, consistency, atau confidence yang tinggi tetap tidak otomatis menjadi rekomendasi bisnis atau izin untuk melewati master compatibility.",
        ],
        note: "Selalu baca depot, periode, unit analitik, kualitas data, dan evidence count sebelum memakai hasil.",
      },
      {
        id: "doc-alur",
        title: "Alur kerja yang disarankan",
        steps: [
          { title: "Bangun fondasi data", text: "Import dan validasi MT, SPBU, Loading Order, produk, depot, tag, dan GPS pada Master Data." },
          { title: "Periksa Tag Consistency", text: "Pastikan observed Loading Order assignment lolos Vehicle Class dan tag requirement." },
          { title: "Baca pola historis", text: "Gunakan Phase 2 untuk departure, Phase 3 untuk pairing, dan Phase 4 untuk affinity serta stability." },
          { title: "Bangun model Phase 5", text: "Readiness wajib lolos sebelum anomaly analysis atau behavioral clustering dijalankan." },
          { title: "Jalankan Phase 6", text: "Pilih model tersimpan, upload LO dan MT availability, validasi, lalu jalankan prediction." },
          { title: "Kendalikan operasi di Phase 7", text: "Buat Job per depot/tanggal, load Prediction Run tersimpan, masukkan actual MT/bay/queue, optimalkan V1, lalu reroute ke versi baru saat kondisi berubah." },
        ],
      },
      {
        id: "doc-konvensi",
        title: "Konvensi membaca halaman",
        cards: [
          { name: "Apply", meaning: "Perubahan filter belum memengaruhi hasil sebelum tombol ditekan.", reading: "Periksa depot dan tanggal, lalu tekan Apply." },
          { name: "Confidence", meaning: "Kekuatan evidence atau membership, bukan kepastian outcome.", reading: "Baca bersama observation atau evidence count." },
          { name: "Empty state", meaning: "Analisis belum dijalankan atau scope tidak menghasilkan data.", reading: "Lengkapi filter wajib; jangan langsung menyimpulkan data kosong." },
          { name: "Warning", meaning: "Hasil dapat ditinjau tetapi memiliki keterbatasan atau fallback.", reading: "Baca diagnostic sebelum dipakai operasional." },
          { name: "Error", meaning: "Input atau konfigurasi invalid dan proses diblokir.", reading: "Perbaiki seluruh error sebelum melanjutkan." },
        ],
      },
    ],
  },
  {
    id: "doc-dashboard",
    number: "02",
    title: "Dashboard",
    description: "Ringkasan kesehatan fondasi data dan coverage operasional.",
    page: "dashboard",
    topics: [
      {
        id: "doc-dashboard-pakai",
        title: "Fungsi dan cara menggunakan",
        paragraphs: [
          "Pilih All Depots untuk gambaran lintas depot atau satu depot untuk audit lokal, lalu tekan Apply Filter. Gunakan halaman ini sebagai health check sebelum membuka fase analitik.",
        ],
      },
      {
        id: "doc-dashboard-card",
        title: "Cara membaca KPI, chart, dan card",
        cards: [
          { name: "Total MT / Active MT", meaning: "Seluruh MT / MT berstatus aktif.", reading: "Selisih adalah MT nonaktif yang tetap dipertahankan untuk histori." },
          { name: "Total SPBU / Active SPBU", meaning: "Seluruh SPBU / SPBU aktif.", reading: "SPBU nonaktif dapat muncul pada histori, tetapi bukan target aktif." },
          { name: "Depots, Products, Tags, Tag Types", meaning: "Distinct canonical master pada scope.", reading: "Nilai nol menunjukkan referensi belum diimport atau belum terpetakan." },
          { name: "LO Lines / Shipments", meaning: "Baris LO / distinct shipment hasil grouping.", reading: "LO Lines biasanya lebih besar karena satu shipment dapat memuat beberapa line." },
          { name: "Unique MT / SPBU in LO", meaning: "Distinct entity yang pernah muncul pada LO.", reading: "Bandingkan dengan master aktif untuk melihat historical coverage." },
          { name: "Unmatched MT / SPBU", meaning: "Referensi LO yang gagal dipetakan ke master.", reading: "Nilai sebaiknya menuju nol sebelum analisis lanjutan." },
          { name: "GPS Events / GPS Visits", meaning: "Event GPS mentah / kunjungan yang berhasil direkonstruksi.", reading: "Tidak semua titik GPS adalah kunjungan, sehingga nilainya tidak sama." },
          { name: "Quality Issues", meaning: "Jumlah issue dari rule kualitas data.", reading: "Buka Data Quality Explorer untuk severity dan record terdampak." },
          { name: "Tag Distribution Charts", meaning: "Jumlah MT/SPBU per Vehicle Class dan Project Tag.", reading: "Bandingkan sisi MT dan SPBU untuk melihat imbalance." },
          { name: "SPBU per Shipment / Product", meaning: "Kompleksitas shipment dan komposisi produk.", reading: "Bar/pie terbesar menunjukkan pola paling dominan pada scope." },
          { name: "Reference Mapping Coverage", meaning: "Mapped dibanding seluruh reference yang diperiksa.", reading: "Coverage tinggi meningkatkan eligibility analisis." },
          { name: "Compatibility Summary", meaning: "Compatible, incompatible, insufficient-data untuk pasangan MT–SPBU.", reading: "Incompatible adalah hasil rule, bukan error aplikasi." },
          { name: "Trip Reconstruction Validator", meaning: "Ketersediaan sequence kunjungan dari GPS.", reading: "NO_GPS_SEQUENCE berarti urutan belum dapat dibuktikan." },
        ],
      },
      {
        id: "doc-dashboard-hitung",
        title: "Perhitungan dan contoh",
        formulas: [
          "Coverage master = distinct entity yang muncul di LO / entity master aktif × 100%\nMapping coverage = reference mapped / seluruh reference yang diperiksa × 100%\nSPBU per shipment = distinct SPBU dalam shipment",
        ],
        examples: [
          { title: "Coverage MT", text: "Master memiliki 80 MT aktif dan 60 muncul pada LO. Coverage = 60 ÷ 80 × 100% = 75%. Ini bukan utilization rate; hanya coverage historis." },
        ],
      },
    ],
  },
  {
    id: "doc-master-data",
    number: "03",
    title: "Master Data Management",
    description: "Mengelola sumber canonical yang menjadi dasar seluruh analisis.",
    page: "master-data",
    topics: [
      {
        id: "doc-master-pakai",
        title: "Cara import, export, dan CRUD",
        steps: [
          { title: "Pilih domain", text: "Mobil Tangki, SPBU, Loading Order, atau GPS." },
          { title: "Gunakan template", text: "Download template agar nama dan tipe kolom sesuai validator." },
          { title: "Import file", text: "Pilih sheet dan upload XLSX/CSV. Sistem melakukan staging, validation, normalization, lalu publish." },
          { title: "Audit history", text: "Periksa total, valid, warning, rejected, dan status batch." },
          { title: "Perbaiki melalui CRUD", text: "Cari, filter, sort, pilih row, lalu Add, Edit, atau Delete." },
        ],
      },
      {
        id: "doc-master-card",
        title: "Cara membaca setiap card",
        cards: [
          { name: "Import Data", meaning: "Upload file, template, refresh, dan sample data.", reading: "Pilih domain dan sheet yang sesuai sebelum memilih file." },
          { name: "Export Data Per Depot", meaning: "Unduh canonical data menurut domain/depot.", reading: "All Data menggunakan XLSX; single domain dapat XLSX/CSV." },
          { name: "Import History", meaning: "Audit seluruh batch import.", reading: "Valid publishable; warning perlu review; rejected tidak dipublish." },
          { name: "Master Data CRUD", meaning: "Kelola Depot, Product, Tag, MT, SPBU, Shipment, dan LO.", reading: "Gunakan search-column, depot/status filter, sorting, pagination, dan batch selection." },
          { name: "Modal Add / Edit", meaning: "Menambah beberapa row atau memperbaiki record.", reading: "Field wajib harus lengkap; ID tertentu read-only saat edit." },
          { name: "Dynamic Tag Columns", meaning: "Tag mengikuti tag type aktif.", reading: "Tag MT/SPBU dapat diedit tanpa mengubah struktur tabel." },
        ],
      },
      {
        id: "doc-master-contoh",
        title: "Validasi dan contoh",
        formulas: [
          "Valid rows + Warning rows + Rejected rows = Total rows\nPublishable rows = Valid rows + Warning rows, jika warning bersifat non-blocking",
        ],
        examples: [
          { title: "Audit import", text: "Dari 1.000 row: 920 valid, 60 warning, 20 rejected. Completeness publishable maksimum = 980 ÷ 1.000 = 98%. Dua puluh row perlu diperbaiki." },
          { title: "Koordinat SPBU", text: "Input 5,19182389869645 96,4368560343681 dibaca sebagai latitude 5.19182389869645 dan longitude 96.4368560343681. Urutan wajib latitude lalu longitude." },
        ],
      },
    ],
  },
  {
    id: "doc-tag",
    number: "04",
    title: "Tag Consistency Analysis",
    description: "Memeriksa assignment LO terhadap Vehicle Class dan kebutuhan tag SPBU.",
    page: "tag-consistency",
    topics: [
      {
        id: "doc-tag-pakai",
        title: "Fungsi dan cara menggunakan",
        paragraphs: [
          "Pilih tanggal, depot, SPBU, kendaraan, tag type, status, product, vehicle class, atau search. Klik row evidence untuk membuka Loading Order Tag Analysis dan melihat rule per tag.",
        ],
      },
      {
        id: "doc-tag-card",
        title: "KPI, chart, dan evidence",
        cards: [
          { name: "Total LO Assignments", meaning: "Seluruh assignment pada scope.", reading: "Denominator awal sebelum Data Issues dipisahkan." },
          { name: "Matched", meaning: "Assignment memenuhi seluruh rule.", reading: "Semakin tinggi semakin konsisten terhadap master rule." },
          { name: "Mismatch", meaning: "Data cukup, tetapi minimal satu rule gagal.", reading: "Periksa tag type/value dan Vehicle Class Result." },
          { name: "Data Issues", meaning: "Assignment tidak dapat dievaluasi karena data hilang/unmapped.", reading: "Bukan mismatch bisnis; perbaiki fondasi data." },
          { name: "Analyzable LO", meaning: "Matched + Mismatch.", reading: "Menjadi denominator Consistency Rate." },
          { name: "Consistency Rate", meaning: "Matched dibagi Analyzable LO.", reading: "Data Issues tidak dimasukkan agar mapping error tidak dianggap mismatch." },
          { name: "Mismatch by Tag Type / Value", meaning: "Frekuensi gagal menurut dimensi tag.", reading: "Bar terbesar adalah sumber mismatch terbanyak." },
          { name: "Daily Consistency Rate", meaning: "Rate per hari.", reading: "Cari penurunan mendadak atau tren memburuk." },
          { name: "Data Quality Issues", meaning: "Komposisi issue penghambat evaluasi.", reading: "Gunakan untuk prioritas perbaikan master." },
          { name: "Top SPBU / MT Mismatch", meaning: "Ranking count dan mismatch rate.", reading: "Baca rate bersama total assignment agar sampel kecil tidak dilebihkan." },
          { name: "Evidence Table", meaning: "Row LO pembentuk hasil.", reading: "Klik untuk melihat requirement, available tag, missing tag, result, dan reason." },
        ],
      },
      {
        id: "doc-tag-hitung",
        title: "Rumus dan contoh",
        formulas: [
          "Analyzable LO = Matched + Mismatch\nConsistency Rate = Matched / Analyzable LO × 100%\nVehicle Class PASS jika kelas/kapasitas MT ≤ batas maksimum SPBU\nTag PASS jika seluruh required tag SPBU adalah subset tag MT",
        ],
        examples: [
          { title: "Consistency Rate", text: "100 assignment terdiri dari 80 Match, 15 Mismatch, dan 5 Data Issue. Analyzable = 95. Rate = 80 ÷ 95 × 100% = 84,21%." },
          { title: "Subset tag", text: "SPBU membutuhkan PROJECT_A dan SAFETY_X; MT memiliki PROJECT_A, SAFETY_X, dan NIGHT. Hasil PASS karena seluruh requirement tersedia; extra tag tidak membuat mismatch." },
        ],
      },
    ],
  },
  {
    id: "doc-phase2",
    number: "05",
    title: "Phase 2 · Depot Departure Time",
    description: "Pola waktu keberangkatan historis dan affinity terhadap shift operasional.",
    page: "departure-intelligence",
    topics: [
      {
        id: "doc-phase2-pakai",
        title: "Fungsi dan cara menggunakan",
        paragraphs: [
          "Pilih Depot, Start Date, End Date, bucket 30/60 menit, dan metode shift. Konfigurasi shift harus menutup 24 jam tanpa overlap, lalu tekan Apply. Analisis tidak berjalan otomatis saat halaman dibuka.",
        ],
      },
      {
        id: "doc-phase2-card",
        title: "Cara membaca seluruh card",
        cards: [
          { name: "Observations", meaning: "Distinct shipment_id + spbu_id dengan timestamp usable.", reading: "Repeated LO/product tidak menggandakan observation." },
          { name: "SPBU Profiles / Shipments / Vehicles", meaning: "Jumlah profile dan distinct entity.", reading: "Menilai luas scope analisis." },
          { name: "Quantity / Missing Timestamps", meaning: "Volume dispatch / record tanpa timestamp valid.", reading: "Missing tinggi menurunkan coverage." },
          { name: "Timestamp Source Coverage", meaning: "Persentase GPS dan fallback LO gate-out.", reading: "GPS diprioritaskan bila reliabel; LO menjaga coverage." },
          { name: "Confidence Mix", meaning: "Jumlah profile HIGH/MEDIUM/LOW.", reading: "Klik level untuk memfilter tabel profile." },
          { name: "24-Hour Distribution", meaning: "Frekuensi departure per bucket.", reading: "Peak adalah pola historis, bukan jadwal wajib." },
          { name: "Weekday Heatmap", meaning: "Intensitas departure menurut hari dan jam.", reading: "Cari pola shift dan perbedaan weekday." },
          { name: "SPBU Box Plot", meaning: "P25, median, P75, whisker/outlier pada circular time.", reading: "Box sempit berarti waktu lebih konsisten." },
          { name: "Operational Shift Summary", meaning: "Jumlah SPBU per primary shift/status.", reading: "Klik untuk memfilter profile dan heatmap." },
          { name: "Shift Affinity Heatmap", meaning: "Share observation SPBU per shift.", reading: "Satu row idealnya 100%; warna memakai share, bukan count." },
          { name: "Departure Profiles", meaning: "P20–P80, P50, peak, IQR, count, confidence, shift.", reading: "Klik row untuk membuka source lineage." },
          { name: "SPBU Explorer · Source Lineage", meaning: "LO gate-out, GPS exit, timestamp used, source, selisih.", reading: "Audit asal timestamp pembentuk profile." },
        ],
      },
      {
        id: "doc-phase2-hitung",
        title: "Percentile, circular time, dan shift",
        paragraphs: [
          "Waktu adalah lingkaran 24 jam. Sistem mencari gap terbesar, memotong lingkaran pada gap tersebut, menghitung percentile pada seri linear, lalu mengembalikan hasil ke HH:mm. Karena itu 23:50 dan 00:10 tidak terlihat berjarak hampir 24 jam.",
        ],
        formulas: [
          "departure_datetime_used = reliable GPS depot exit; fallback ke LO gate-out\nPreferred Historical Window = circular P20 sampai P80\nIQR dispersion = P75 - P25 pada seri yang sudah di-unwrap\nShift affinity(s) = observation dalam shift s / seluruh observation SPBU × 100%",
          "Hybrid = 0,40 × shift share + 0,25 × window overlap + 0,20 × median alignment + 0,15 × peak alignment\nConfidence factor: HIGH 1,00 · MEDIUM 0,80 · LOW 0,60",
        ],
        examples: [
          { title: "Shift affinity", text: "Dari 50 observation: Shift 1=34, Shift 2=12, Shift 3=3, Shift 4=1. Share = 68%, 24%, 6%, 2%. Dominant Shift memilih Shift 1." },
        ],
      },
    ],
  },
  {
    id: "doc-phase3",
    number: "06",
    title: "Phase 3 · SPBU Pairing",
    description: "Seberapa sering dua SPBU berada dalam shipment yang sama.",
    page: "pairing-intelligence",
    topics: [
      {
        id: "doc-phase3-pakai",
        title: "Fungsi dan cara menggunakan",
        paragraphs: [
          "Pilih Depot, preset/custom date range, Product, dan optional search lalu tekan Apply. Pair A–B bersifat unordered untuk identitas pair, sedangkan conditional probability tetap directional.",
        ],
      },
      {
        id: "doc-phase3-card",
        title: "KPI, matrix, network, dan detail",
        cards: [
          { name: "Total Shipments", meaning: "Distinct shipment eligible.", reading: "Denominator Support." },
          { name: "Multi-SPBU Shipments", meaning: "Shipment dengan minimal dua SPBU.", reading: "Hanya shipment ini dapat membentuk pair." },
          { name: "Unique SPBU / Pairs", meaning: "Distinct SPBU dan canonical unordered pair.", reading: "A–B hanya dihitung sekali." },
          { name: "High-Confidence Pairs", meaning: "Pair dengan evidence HIGH.", reading: "Bukan daftar pair terbaik; hanya evidence kuat." },
          { name: "Avg SPBU / Shipment", meaning: "Total membership SPBU dibagi shipment.", reading: "Tinggi berarti shipment rata-rata lebih kompleks." },
          { name: "Data Quality", meaning: "Source, eligible, excluded, reason.", reading: "Selisih source–eligible tidak dianalisis." },
          { name: "Probability Distribution", meaning: "Distribusi pair menurut probability bucket.", reading: "Lihat apakah mayoritas hubungan lemah atau kuat." },
          { name: "Top Pairings", meaning: "Count, directional probability, support, lift, confidence.", reading: "Klik row untuk detail dan evidence." },
          { name: "Pairing Matrix", meaning: "Kekuatan relationship antar-SPBU.", reading: "Sel lebih pekat berarti metric lebih tinggi." },
          { name: "Pairing Network", meaning: "Node SPBU dan edge same-shipment.", reading: "Edge bukan urutan rute." },
          { name: "Detail / Evidence", meaning: "Metric directional dan shipment pembentuk pair.", reading: "Audit tanggal, shipment, produk, MT, dan GPS transition terpisah." },
        ],
      },
      {
        id: "doc-phase3-hitung",
        title: "P(B|A), support, lift, dan confidence",
        formulas: [
          "P(B|A) = shipment(A dan B) / shipment(A)\nP(A|B) = shipment(A dan B) / shipment(B)\nSupport(A,B) = shipment(A dan B) / seluruh eligible shipment\nP(B) = shipment(B) / seluruh eligible shipment\nLift(A,B) = P(B|A) / P(B)",
          "INSUFFICIENT jika min(count A, count B) < 5 atau pair_count < 3\nLOW jika pair_count 3–9 · MEDIUM 10–29 · HIGH ≥ 30",
        ],
        examples: [
          { title: "Pair A–B", text: "Dari 100 shipment: A=40, B=25, A+B=10. P(B|A)=25%; P(A|B)=40%; Support=10%; P(B)=25%; Lift=1,00. Pair_count 10 menghasilkan MEDIUM bila syarat minimum terpenuhi." },
        ],
        note: "A–B berarti same-shipment. A→B berarti consecutive GPS visit. Shipment A→C→B membentuk pair A–C, A–B, C–B, tetapi transition hanya A→C dan C→B.",
      },
    ],
  },
  {
    id: "doc-phase4",
    number: "07",
    title: "Phase 4 · SPBU–MT Affinity & Stability",
    description: "Distribusi historis kendaraan yang melayani SPBU dan kestabilannya.",
    page: "affinity-intelligence",
    topics: [
      {
        id: "doc-phase4-pakai",
        title: "Fungsi dan cara menggunakan",
        paragraphs: [
          "Pilih Depot, SPBU, date range, Product, minimum observations, Confidence, temporal bucket, recent period, Top MT, dan edge metric lalu Apply. Search SPBU menentukan fokus detail; KPI tetap merangkum semua SPBU eligible.",
        ],
      },
      {
        id: "doc-phase4-card",
        title: "Cara membaca seluruh card",
        cards: [
          { name: "KPI Summary", meaning: "Eligible Shipment, SPBU, MT, pair, Avg/Median fleet, consistency, variability, stability, shift.", reading: "Seluruh KPI mengikuti filter yang sudah di-Apply." },
          { name: "Data Quality Summary", meaning: "Source, eligible, excluded, duplicate removed, bucket, algorithm.", reading: "Eligible % menjelaskan analytical coverage." },
          { name: "Historical Profile", meaning: "Dominant MT/share, Top-3, pattern, four scores.", reading: "Consistency, Variability, Stability, dan Confidence dibaca terpisah." },
          { name: "MT Historical Probability", meaning: "P(MT|SPBU).", reading: "Dominant historical MT bukan rekomendasi." },
          { name: "Affinity Over Time", meaning: "Probability per temporal bucket.", reading: "Crossing line dapat menandai pattern shift." },
          { name: "Recent vs Full Period", meaning: "Distribusi recent dibanding seluruh periode.", reading: "Tidak ada hidden recency weighting." },
          { name: "Reverse Affinity", meaning: "P(SPBU|MT).", reading: "Total dapat >100% karena satu shipment MT melayani beberapa SPBU." },
          { name: "Consistency Scatter", meaning: "X=Unique MT, Y=Consistency, size=shipment, color=confidence.", reading: "Kanan bawah adalah footprint luas dengan distribusi tersebar." },
          { name: "Pattern Matrix", meaning: "X=Unique MT, Y=Dominant Affinity.", reading: "Quadrant Dedicated-like, Preferred-fleet, Limited-balanced, Highly-flexible." },
          { name: "Ranking Cards", meaning: "Most Consistent, Variable, Pattern Change.", reading: "Baca bersama shipment count dan confidence." },
          { name: "Bipartite Network", meaning: "Node SPBU/MT dan edge relationship.", reading: "Thickness mengikuti Shipment Count atau Affinity Probability." },
          { name: "Historical Evidence", meaning: "Shipment pembentuk relationship aktif.", reading: "Distinct shipment header harus sama dengan relationship count." },
          { name: "Methodology & Guardrails", meaning: "Formula dan algorithm version.", reading: "Gunakan untuk audit reproducibility." },
        ],
      },
      {
        id: "doc-phase4-perbandingan",
        title: "Perbedaan grafik dan ranking SPBU",
        paragraphs: [
          "SPBU Consistency Scatter Plot dan Historical Pattern Matrix sama-sama menampilkan satu titik untuk setiap SPBU eligible dalam scope filter aktif, tetapi menjawab pertanyaan berbeda. Scatter Plot mengukur konsentrasi seluruh distribusi penggunaan MT, sedangkan Pattern Matrix membuat klasifikasi operasional dari jumlah MT unik dan affinity MT nomor satu.",
          "Dua SPBU dapat berada dalam quadrant Pattern Matrix yang sama tetapi mempunyai Consistency Score berbeda karena Matrix hanya memakai dominant affinity dan jumlah MT unik, sementara Consistency memakai seluruh probability distribution MT melalui normalized HHI.",
          "Tiga ranking card tidak memilih SPBU terbaik dan bukan rekomendasi assignment. Ranking hanya mengurutkan perilaku historis menurut concentration, distribution breadth, atau perubahan antarperiode dalam scope yang sudah di-Apply.",
        ],
        cards: [
          { name: "SPBU Consistency Scatter Plot", meaning: "X = Unique MT, Y = Consistency Score seluruh distribusi, ukuran = shipment, warna = confidence.", reading: "Semakin tinggi titik, semakin terkonsentrasi penggunaan pada sedikit MT; kanan bawah menunjukkan footprint luas dengan distribusi tersebar." },
          { name: "Historical Pattern Matrix", meaning: "X = Unique MT, Y = Dominant MT Affinity; garis 60% dan median Unique MT membentuk empat quadrant.", reading: "Kiri atas Dedicated-like, kanan atas Preferred-fleet, kiri bawah Limited-balanced, dan kanan bawah Highly-flexible." },
          { name: "Most Historically Consistent SPBU", meaning: "Urutan Consistency Score tertinggi, lalu shipment terbanyak dan kode SPBU.", reading: "Posisi atas berarti penggunaan MT paling terkonsentrasi; baca bersama Unique MT, Dominant %, Top-3 Share, shipment, dan confidence." },
          { name: "Most Historically Variable SPBU", meaning: "Urutan Variability Score atau normalized entropy tertinggi, lalu shipment terbanyak dan kode SPBU.", reading: "Posisi atas berarti shipment lebih merata tersebar ke berbagai MT; ini tidak otomatis berarti pola sering berubah antarperiode." },
          { name: "Highest Historical Pattern Change", meaning: "Urutan Temporal Stability terendah, lalu Pattern Shift Distance terbesar dan shipment terbanyak.", reading: "Posisi atas berarti distribusi MT paling berubah antar-bucket waktu; gunakan Previous MT, Recent MT, dan shift level untuk membaca arah perubahan." },
        ],
        examples: [
          { title: "Variable bukan selalu berubah", text: "SPBU dapat memakai banyak MT secara merata setiap minggu. Variability-nya tinggi karena distribusi tersebar, tetapi Temporal Stability tetap tinggi karena pola mingguannya konsisten." },
          { title: "Pattern change bukan selalu banyak MT", text: "SPBU dapat hanya memakai dua MT, tetapi dominant MT berganti tajam antara periode sebelumnya dan terbaru. Unique MT tetap rendah, sementara Pattern Change dapat tinggi." },
        ],
        note: "Selalu baca ranking bersama shipment count dan confidence agar SPBU dengan evidence kecil tidak disamakan dengan SPBU ber-evidence kuat.",
      },
      {
        id: "doc-phase4-hitung",
        title: "Probability, HHI, entropy, confidence, dan stability",
        formulas: [
          "pᵢ = shipment SPBU dengan MT i / seluruh shipment SPBU\nTop3 Share = p₁ + p₂ + p₃\nHHI = Σpᵢ²\nNormalized HHI = (HHI - 1/N) / (1 - 1/N)\nConsistency = 100 × Normalized HHI\nEntropy = -Σ(pᵢ × ln pᵢ)\nVariability = 100 × Entropy / ln(N)",
          "Evidence Confidence = 40% sample + 20% operating day + 15% date span + 10% recency + 15% active bucket\nLOW < 40 · MEDIUM 40–<70 · HIGH ≥ 70",
          "Temporal Stability = 100 × [0,70 × (1 - mean consecutive JS distance) + 0,30 × modal dominant persistence]\nShift: ≤0,10 STABLE · ≤0,25 MINOR · ≤0,50 MODERATE · >0,50 MAJOR",
        ],
        examples: [
          { title: "HHI", text: "T01=60%, T02=30%, T03=10%. HHI=0,46. N=3, Normalized HHI=(0,46−0,3333)/(1−0,3333)=0,19; Consistency=19%." },
          { title: "Stability", text: "Mean JS distance=0,20 dan persistence=0,75. Stability=100×[0,70×0,80 + 0,30×0,75] = 78,5." },
        ],
      },
    ],
  },
  {
    id: "doc-phase5",
    number: "08",
    title: "Phase 5 · Machine Learning",
    description: "Anomaly concentration dan reusable behavioral clustering.",
    page: "machine-learning-intelligence",
    topics: [
      {
        id: "doc-phase5-pakai",
        title: "Readiness dan workflow",
        paragraphs: [
          "Readiness lolos hanya jika terdapat assignment yang dievaluasi dan seluruh observed assignment Phase 1 berstatus MATCH. Gate diulang server-side saat analyze, prepare, train, save, dan activate.",
          "Training behavioral hanya memakai SPBU dengan histori yang memenuhi minimum observation. Setelah model terbentuk, seluruh SPBU master aktif lainnya dipetakan secara konservatif ke cluster terdekat sebagai cold-start coverage; record ini tidak ikut menghitung statistik behavioral, comparison, atau Data Demo Phase 6.",
        ],
        cards: [
          { name: "Engine A", meaning: "Pilih baseline dan minimum observation lalu Analyze.", reading: "Buka ranking/detail atau saved analysis run." },
          { name: "Engine B", meaning: "Prepare → Validate → Configure → Train → Review → Save.", reading: "Training result belum menjadi registry model sebelum disimpan." },
          { name: "Model Registry", meaning: "Open, Activate, Duplicate, Archive/Delete, Compare.", reading: "Hanya satu model ACTIVE per depot; version tidak overwrite." },
        ],
      },
      {
        id: "doc-phase5-card",
        title: "Cara membaca Engine A dan Engine B",
        cards: [
          { name: "Readiness", meaning: "Exact observed compatibility count.", reading: "Pembulatan 100% UI tidak dapat melewati gate bila count gagal." },
          { name: "Engine A KPI", meaning: "Analyzed, Sufficient, Insufficient, Investigation.", reading: "Investigation bukan bukti kesalahan dispatcher." },
          { name: "Anomaly Ranking", meaning: "Score relatif 0–100 dalam satu run.", reading: "Bandingkan hanya dalam baseline/run yang sama." },
          { name: "Compatibility vs Usage", meaning: "X=compatible opportunity, Y=used MT.", reading: "Used jauh lebih kecil memberi konteks konsentrasi." },
          { name: "Dominant Share vs Breadth", meaning: "Concentration dua dimensi.", reading: "Breadth rendah + dominant tinggi berarti footprint sempit." },
          { name: "Concentration Detail", meaning: "Breadth, dominant, HHI, entropy, distribution, peer.", reading: "Unused compatible MT bukan otomatis harus dipakai." },
          { name: "Training Dataset", meaning: "Shipment, historical training, total active coverage, no-history, insufficient-history, inactive, dan edges.", reading: "Hanya sufficient-history SPBU yang memengaruhi training; cold start dipetakan sesudahnya." },
          { name: "UMAP Cluster Map", meaning: "Kemiripan behavior.", reading: "Jarak plot bukan jarak geografis." },
          { name: "Geographic Map", meaning: "Koordinat aktual berwarna cluster.", reading: "Koordinat tidak masuk feature clustering." },
          { name: "Cluster Profiles", meaning: "Historical members, cold-start members, common tags, shift, pair, membership.", reading: "Statistik behavioral hanya dihitung dari historical members; common tag minimal 50% anggota historis." },
          { name: "Membership", meaning: "HDBSCAN membership, noise, coverage source, dan history eligibility.", reading: "Cold-start memiliki membership konservatif dan bukan behavioral evidence." },
          { name: "Registry / Compare", meaning: "Versioned artifact dan comparison.", reading: "Matching memakai Jaccard + Hungarian, bukan raw cluster ID." },
        ],
      },
      {
        id: "doc-phase5-hitung",
        title: "Perhitungan dan contoh ML",
        formulas: [
          "Utilization breadth = historically used compatible MT / compatible MT\nPairing graph weight(A,B) = [P(B|A) + P(A|B)] / 2\nAnomaly score = (raw severity - min raw) / (max raw - min raw) × 100\nJika seluruh raw severity sama, seluruh score = 0",
          "Feature group scaling = standardized group × √(weight / group dimension)\nJaccard(A,B) = |member A ∩ member B| / |member A ∪ member B|",
        ],
        examples: [
          { title: "Engine A", text: "20 MT compatible dan 4 historically used menghasilkan breadth 20%. Final anomaly tetap relatif terhadap SPBU peer pada run yang sama." },
          { title: "Jaccard", text: "Cluster v1={A,B,C,D}; v2={B,C,D,E}. Intersection=3 dan union=5, sehingga similarity=60%." },
        ],
      },
    ],
  },
  {
    id: "doc-phase6",
    number: "09",
    title: "Phase 6 · Prediction & Assignment",
    description: "Grouping LO, MT ranking, rolling multi-trip assignment, dan availability.",
    page: "prediction-assignment",
    topics: [
      {
        id: "doc-phase6-pakai",
        title: "Workflow menjalankan prediction",
        steps: [
          { title: "Prediction Setup", text: "Pilih depot, max pairing gap, assignment mode, max delay, model, dan minimum confidence." },
          { title: "Review Model", text: "Pastikan depot, version, training period, cluster, dan average membership sesuai." },
          { title: "Siapkan input", text: "LO memuat nomor, waktu, SPBU, Product canonical, dan tepat 8 KL per row; Data Demo LO hanya mengambil SPBU behavioral-history pada model. Setelah upload/demo, kelola row LO melalui Add, Edit, Delete, filter, sort, dan pagination. Untuk MT, klik Import Data from Master Data lalu atur status operasional Active/Deactive dan ETA on Depot di MT Management; perubahan divalidasi ulang sebagai input aktif." },
          { title: "Validation Result", text: "ERROR memblokir Run; WARNING tetap dapat direview." },
          { title: "Run Prediction", text: "Task masuk queue. Pantau QUEUED/RUNNING hingga COMPLETED atau FAILED." },
        ],
      },
      {
        id: "doc-phase6-card",
        title: "Cara membaca card 1–12",
        cards: [
          { name: "1. Prediction Setup", meaning: "Scope dan policy run.", reading: "STRICT_START tepat waktu; ALLOW_DELAY sampai Max Delay." },
          { name: "2. Model Information", meaning: "Snapshot model Phase 5.", reading: "Phase 6 tidak melakukan training." },
          { name: "3. LO Upload", meaning: "LO, start time, SPBU, Product, quantity tepat 8 KL.", reading: "Product dipetakan ke Master Product; Data Demo membagi total KL ÷ 8 dan hanya memilih SPBU model dengan history_eligible=true." },
          { name: "3A. LO Management", meaning: "Editor row LO dan Product sebelum prediction.", reading: "Add/Edit memakai dropdown Master SPBU depot dan Master Product aktif, bukan free text. Perubahan membentuk ulang workbook aktif dan validasi backend; gunakan filter Product/per kolom, sort header, dan pagination untuk review." },
          { name: "4. MT Availability", meaning: "Import MT canonical aktif dari depot terpilih.", reading: "Tidak ada upload Excel, template, atau Data Demo; Import Data from Master Data membuka MT Management." },
          { name: "4A. MT Management", meaning: "No MT, MT Tag Class, status operasional, dan ETA on Depot.", reading: "Tidak ada Add/Delete. MT eligible default Active dan MT dengan profil kapasitas tidak valid default Deactive; semua ETA memakai awal Shift 1. Deactive mengecualikan MT dari prediction tanpa mengubah Master MT." },
          { name: "5. Validation", meaning: "Severity, code, row, message.", reading: "Selesaikan ERROR sebelum Run." },
          { name: "6. Summary", meaning: "LO, KL, SPBU, shipment, MT, assigned, delay, unassigned, multi-trip, fallback, confidence.", reading: "Tabel shift dan cache metrics melengkapi total." },
          { name: "7–8. Shipment & Assignment", meaning: "Grouping, MT, compartment, timeline, score, status.", reading: "Expand untuk melihat SPBU + cluster, explanation, route, candidate, exclusion, dan override. Move to menampilkan shipment, SPBU, serta cluster target." },
          { name: "MT Multi-Trip Timeline", meaning: "Bar departure sampai turnaround.", reading: "Bar satu MT tidak boleh overlap." },
          { name: "9A. Prediction Network", meaning: "Edge predicted same shipment.", reading: "Bukan route map." },
          { name: "9B. Assignment Matrix", meaning: "Affinity score, compatibility, selected MT.", reading: "X incompatible; outline assigned." },
          { name: "10A. KL per Jam", meaning: "Assigned KL per departure hour dan cumulative.", reading: "Unassigned shipment tidak dihitung." },
          { name: "10B. Geographic Route", meaning: "Master markers dan route geometry per MT.", reading: "Cari nomor MT untuk mempersempit select; solid=road geometry dan dashed=fallback." },
          { name: "11. Run History", meaning: "Immutable run, status, View, Export, Re-run, dan pagination.", reading: "Pilih 10/25/50 row; failed run dapat retry dari saved input." },
          { name: "12. Export", meaning: "Workbook audit lengkap.", reading: "Berisi Summary, Result, Timeline, Assignment, Candidate, Validation." },
        ],
      },
      {
        id: "doc-phase6-hitung",
        title: "Grouping, assignment, dan cycle time",
        paragraphs: [
          "Algoritma v9 mencoba shipment dan assignment secara berurutan: 32 KL/4 LO, 24 KL/3 LO, 16 KL/2 LO, lalu 8 KL/1 LO. Grup besar yang tidak berhasil assigned dibongkar dan LO diteruskan ke tier lebih kecil. Grouping tetap mempertimbangkan derived shift, gap waktu, minimum pairing confidence, cluster/pairing evidence, tag, route feasibility, dan non-overlap set packing.",
          "Exact full-load wajib: MT 32/24/16/8 KL hanya boleh menjalankan shipment dengan kapasitas yang sama. Tidak ada partial-load fallback. Assignment kemudian menjaga rolling availability agar trip MT yang sama tidak overlap.",
        ],
        formulas: [
          "Required compartments = jumlah LO dalam shipment\nTotal order KL = jumlah LO × 8 KL\nEligible capacity jika compartment MT = required compartments\nPlanned start = timestamp LO paling akhir dalam shipment",
          "Total cycle = depot processing + seluruh travel legs + service per stop + return processing\nEstimated return = predicted departure + total cycle\nNext available = estimated return + turnaround buffer",
        ],
        examples: [
          { title: "Iterasi kapasitas", text: "Empat LO membentuk kandidat 32 KL. Jika tidak ada MT 32 KL compatible dan available, grup dibongkar; LO dicoba kembali sebagai kandidat 24 KL, lalu 16 KL, dan terakhir 8 KL." },
          { title: "Cycle time", text: "Departure 08:00; depot 15 menit; travel 150; 2 stop×20; return process 10. Cycle=215 menit; return=11:35. Buffer 30 menit → next available 12:05." },
          { title: "Rolling multi-trip", text: "MT A available 12:05 dan shipment berikutnya planned 12:00. STRICT_START menolak; ALLOW_DELAY 15 menit dapat berangkat 12:05 dengan ASSIGNED_WITH_DELAY." },
        ],
      },
    ],
  },
  {
    id: "doc-phase7",
    number: "10",
    title: "Phase 7 · Dynamic VRP & Bay",
    description: "Optimasi fleet-wide multi-trip, compartment, depot bay queue, rolling reroute, dan route version audit.",
    page: "phase7-optimization",
    topics: [
      {
        id: "doc-phase7-pakai",
        title: "Workflow dispatcher",
        steps: [
          { title: "Buat Job", text: "Pilih depot, operating date, dan nama Job. Seluruh halaman Job menampilkan sticky header dengan sumber, versi, status, LO, MT, trip, dan volume." },
          { title: "Load source Phase 6", text: "Pilih completed Prediction Run dengan depot sama dan tanggal lokal LO yang cocok dengan Operating Date Job. Tanggal pada Run ID adalah tanggal pembuatan run, bukan jaminan tanggal operasional. Source dikunci; predicted shipment/MT/pairing/confidence tidak ditimpa oleh Phase 7." },
          { title: "Lengkapi current state", text: "Load MT canonical, isi Planned ETA Depot atau user override, lalu atur bay master, compatibility product, duration per compartment, occupancy aktual, dan queue fisik." },
          { title: "Pilih parameter dan validasi", text: "Review profile objective/cost/freeze/solver. BLOCKED harus diperbaiki; WARNING Google Routes boleh memakai fallback yang diberi label." },
          { title: "Buat baseline V1", text: "Run Initial Optimization. Review Route Plan, Simulation, Map, Cost, serta Dropped LO sebelum eksekusi." },
          { title: "Update dan reroute", text: "Ubah status LO, MT ETA/status, atau bay queue aktual. Re-Optimize Now membuat V2/V3 tanpa mengubah versi sebelumnya atau menjalankan Phase 6 ulang." },
        ],
      },
      {
        id: "doc-phase7-card",
        title: "Cara membaca halaman Job",
        cards: [
          { name: "Job Management", meaning: "Daftar Job setelah depot dipilih.", reading: "Open Job membuka workspace yang hanya memakai depot dan operating date tersebut." },
          { name: "Job Overview", meaning: "Source run, readiness, initial optimize, dan reroute control.", reading: "Source Phase 6 dikunci setelah load; optimize aktif hanya melalui validasi backend." },
          { name: "LO Management", meaning: "Phase 6 shipment/MT dibanding current Phase 7 assignment dan status.", reading: "PLANNED dapat dioptimalkan; ONGOING/DONE serta freeze-window ditahan." },
          { name: "MT Management", meaning: "Capacity/compartment, Planned/System/User ETA, effective ETA, working time.", reading: "Prioritas ETA: user override → system return → planned initial." },
          { name: "Bay Management", meaning: "Bay, allowed product, jam operasi, arms/mode, duration, occupancy, queue.", reading: "Compatibility product dan queue aktual adalah constraint, bukan dekorasi." },
          { name: "Parameter", meaning: "Objective, solver, freeze, loading, cost, dan soft penalties.", reading: "Save membuat profile version; setiap run menyalin effective snapshot + checksum." },
          { name: "Route Plan", meaning: "MT → Trip → sequence SPBU → LO/product/compartment/ETA.", reading: "Timeline READY–QUEUE–LOADING–GATE OUT–RETURN menunjukkan non-overlap multi-trip." },
          { name: "Simulation", meaning: "Hourly/cumulative gate-out KL serta MT/capacity returning.", reading: "Semua KPI/chart mengikuti route version yang dipilih." },
          { name: "Geographic Map", meaning: "Final route per MT/trip dan stop sequence.", reading: "Solid menunjukkan Google geometry; dashed menunjukkan mixed/master fallback." },
          { name: "Cost & Dropped LO", meaning: "Activation, distance, operating, queue, loading, overtime, penalties, dan unserved reason.", reading: "LO infeasible selalu muncul dengan reason code; tidak pernah hilang diam-diam." },
          { name: "Versions / Audit", meaning: "V1/V2/... berikut parameter checksum, state events, dan plan comparison.", reading: "Versi lama immutable; current pointer hanya menunjuk versi operasional terbaru." },
        ],
      },
      {
        id: "doc-phase7-engine",
        title: "Engine, freeze, dan multi-trip",
        paragraphs: [
          "OR-Tools Routing Solver menentukan assignment MT dan sequence SPBU dari matrix yang sudah dibangun. CP-SAT memeriksa satu product per compartment dan menjadwalkan bay, loading, serta gate-out. Google Routes hanya memberi distance/time/geometry dan tidak pernah menjadi optimizer atau dipanggil dari solver callback.",
          "Satu MT fisik dapat menjalankan Trip 1, kembali ke depot, masuk queue/loading lagi, lalu menjalankan Trip 2 selama availability, working time, compartment, bay, time window, dan depot close masih feasible.",
        ],
        formulas: [
          "Effective MT ETA = User ETA Override → previous System ETA → Initial Planned ETA\nGate-out = Loading Finish + Gate Process Time\nNext trip ready = max(predicted return sebelumnya, actual/user ETA terbaru)",
          "Reroute freeze = DONE + ONGOING + PLANNED dengan gate-out ≤ current time + freeze window\nJika satu LO frozen, seluruh LO pada physical trip yang sama ikut frozen",
          "Total cost = activation + distance + operating + queue + loading + overtime + penalties",
        ],
        examples: [
          { title: "Dropdown Run ID kosong", text: "Periksa depot, status COMPLETED, dan tanggal lokal shipment_start_datetime_local pada snapshot LO. Buat Job dengan operating date yang cocok atau hasilkan completed Phase 6 Run untuk tanggal tersebut; jangan mengubah snapshot/source langsung." },
          { title: "Warm start berubah", text: "Phase 6 memprediksi A+B ke MT01. Phase 7 boleh memindahkan future PLANNED LO ke MT02 jika total objective membaik dan seluruh hard constraint lolos; source prediction MT01 tetap tersimpan." },
          { title: "Actual queue", text: "Bay sedang loading 12 menit dan memiliki dua queue row masing-masing 8 menit. Trip baru tidak dapat mulai loading sebelum blok aktual 28 menit tersebut selesai." },
        ],
        note: "DONE seluruhnya menutup Job tanpa membuat versi baru. Setelah depot close, LO tersisa dicatat UNSERVED_END_OF_DAY untuk keputusan carry-over berikutnya.",
      },
    ],
  },
  {
    id: "doc-maps",
    number: "11",
    title: "Google Maps Integration",
    description: "API key dan parameter route estimation Phase 6.",
    page: "google-maps-integration",
    topics: [
      {
        id: "doc-maps-pakai",
        title: "Konfigurasi dan test koneksi",
        paragraphs: [
          "Pastikan encryption ready, masukkan API key, tekan Save atau Replace Key, lalu Test Connection. Key disimpan encrypted di backend, tidak dikembalikan penuh, dan dapat dicabut dengan Delete Key.",
        ],
      },
      {
        id: "doc-maps-card",
        title: "Arti setiap pengaturan",
        cards: [
          { name: "Routing Mode", meaning: "Tetap DRIVE untuk Indonesia.", reading: "TRUCK/Large Vehicle Routing tidak dikirim." },
          { name: "Traffic Preference", meaning: "Unaware, Aware, atau Aware Optimal.", reading: "Memengaruhi estimasi duration." },
          { name: "Cache TTL", meaning: "Lama result dianggap valid.", reading: "Setelah kedaluwarsa route dapat diminta ulang." },
          { name: "Departure Bucket", meaning: "Pembulatan departure untuk cache key.", reading: "Bucket lebih besar meningkatkan reuse tetapi mengurangi ketepatan waktu." },
          { name: "Depot Processing", meaning: "Waktu proses sebelum perjalanan.", reading: "Masuk total cycle." },
          { name: "SPBU Service / Stop", meaning: "Waktu layanan per stop.", reading: "Dikalikan jumlah SPBU stop." },
          { name: "Return Processing", meaning: "Waktu proses setelah kembali.", reading: "Masuk total cycle." },
          { name: "Turnaround Buffer", meaning: "Buffer setelah estimated return.", reading: "Masuk next available, tidak dihitung dua kali." },
          { name: "Default One-Leg Duration", meaning: "Fallback terakhir per leg.", reading: "Dipakai jika sumber lain tidak tersedia." },
        ],
      },
      {
        id: "doc-maps-hitung",
        title: "Cache dan fallback",
        formulas: [
          "Prioritas: valid route cache / Google Routes → historical SPBU route → cluster historical median → configured default",
        ],
        paragraphs: [
          "Cache key memisahkan origin, destination, departure bucket, traffic preference, DRIVE mode, configuration version, dan vehicle-profile hash. Fallback selalu diberi warning agar tidak dianggap road geometry aktual.",
        ],
      },
    ],
  },
  {
    id: "doc-glosarium",
    number: "12",
    title: "Glosarium dan Guardrail",
    description: "Istilah penting agar hasil tidak salah ditafsirkan.",
    topics: [
      {
        id: "doc-glosarium-istilah",
        title: "Istilah utama",
        cards: [
          { name: "Loading Order", meaning: "Unit order input; Phase 6 wajib 8 KL per LO.", reading: "Beberapa LO dapat menjadi satu shipment." },
          { name: "Shipment", meaning: "Kelompok LO/SPBU untuk satu perjalanan.", reading: "Gunakan distinct shipment sebagai denominator saat disebutkan." },
          { name: "Evidence", meaning: "Record historis pembentuk metric.", reading: "Count kecil membatasi confidence." },
          { name: "Probability", meaning: "Proporsi pada denominator tertentu.", reading: "Selalu identifikasi arah dan denominator." },
          { name: "Confidence", meaning: "Kekuatan evidence/membership.", reading: "Bukan kepastian outcome." },
          { name: "Affinity", meaning: "Frekuensi relationship historis.", reading: "Bukan future suitability." },
          { name: "Stability", meaning: "Konsistensi distribusi antarperiode.", reading: "Tidak menjelaskan penyebab perubahan." },
          { name: "Noise", meaning: "Tidak kuat menjadi anggota cluster HDBSCAN.", reading: "Dapat berarti unique pattern, bukan error." },
          { name: "Fallback", meaning: "Estimasi alternatif saat sumber utama gagal.", reading: "Baca warning dan source." },
          { name: "Phase 6 / 7 Boundary", meaning: "Phase 6 membuat preliminary prediction; Phase 7 membuat final versioned operational plan.", reading: "Phase 7 tidak mengubah source prediction dan tidak menjalankan GMPRO." },
        ],
      },
    ],
  },
];

function flattenTopics() {
  return guides.flatMap((guide) => [
    { id: guide.id, title: guide.title, corpus: guide.description },
    ...guide.topics.map((topic) => ({
      id: topic.id,
      title: topic.title,
      corpus: JSON.stringify(topic),
    })),
  ]);
}
function FormulaBlock({ value }: { value: string }) {
  return <div className="doc-formula"><Calculator size={18} aria-hidden="true" /><pre>{value}</pre></div>;
}

function ExampleBox({ example }: { example: Example }) {
  return <div className="doc-example"><Lightbulb size={18} aria-hidden="true" /><div><strong>{example.title}</strong><p>{example.text}</p></div></div>;
}

function NoteBox({ children }: { children: ReactNode }) {
  return <div className="doc-note"><CircleHelp size={18} aria-hidden="true" /><div>{children}</div></div>;
}

function CardTable({ rows }: { rows: CardRow[] }) {
  return (
    <div className="doc-table-wrap">
      <table className="doc-table">
        <thead><tr><th>Card / Metric</th><th>Fungsi atau perhitungan</th><th>Cara membaca</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.name}><td><strong>{row.name}</strong></td><td>{row.meaning}</td><td>{row.reading}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

type DocumentationPageProps = { onNavigate: (page: AppPage) => void };

export function DocumentationPage({ onNavigate }: DocumentationPageProps) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(guides.map((guide) => guide.id)));
  const searchIndex = useMemo(flattenTopics, []);

  useEffect(() => {
    if (!window.location.hash) return;
    const target = document.getElementById(window.location.hash.slice(1));
    if (target) window.setTimeout(() => target.scrollIntoView({ block: "start" }), 0);
  }, []);

  const searchResults = useMemo(() => {
    const tokens = query.toLocaleLowerCase("id-ID").trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) return [];
    return searchIndex.filter((item) => {
      const corpus = (item.title + " " + item.corpus).toLocaleLowerCase("id-ID");
      return tokens.every((token) => corpus.includes(token));
    });
  }, [query, searchIndex]);

  const visibleGuides = useMemo(() => {
    if (!query.trim()) return guides;
    const resultIds = new Set(searchResults.map((result) => result.id));
    return guides
      .map((guide) => ({ ...guide, topics: guide.topics.filter((topic) => resultIds.has(topic.id)) }))
      .filter((guide) => resultIds.has(guide.id) || guide.topics.length > 0);
  }, [query, searchResults]);

  function jumpTo(id: string) {
    const element = document.getElementById(id);
    if (!element) return;
    window.history.replaceState({}, "", window.location.pathname + "#" + id);
    element.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggleGuide(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <div className="documentation-layout">
      <aside className="documentation-toc" aria-label="Daftar isi dokumentasi">
        <div className="documentation-toc-title"><ListTree size={18} /><div><strong>Daftar Isi</strong><span>{searchIndex.length} topik panduan</span></div></div>
        <label className="documentation-search">
          <Search size={17} aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Cari card, topik, atau rumus…" aria-label="Cari dokumentasi" />
          {query && <button type="button" onClick={() => setQuery("")} aria-label="Hapus pencarian"><X size={15} /></button>}
        </label>
        {query && <div className="documentation-result-count" role="status">{searchResults.length} topik ditemukan</div>}
        <nav className="documentation-tree">
          {visibleGuides.map((guide) => {
            const open = expanded.has(guide.id) || Boolean(query.trim());
            return (
              <div className="documentation-tree-group" key={guide.id}>
                <div className="documentation-tree-row">
                  <button type="button" className="documentation-tree-toggle" onClick={() => toggleGuide(guide.id)} aria-label={(open ? "Tutup " : "Buka ") + guide.title}>{open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
                  <button type="button" className="documentation-tree-link" onClick={() => jumpTo(guide.id)}>{guide.number}. {guide.title}</button>
                </div>
                {open && <div className="documentation-tree-children">{guide.topics.map((topic) => <button type="button" key={topic.id} onClick={() => jumpTo(topic.id)}><span>•</span>{topic.title}</button>)}</div>}
              </div>
            );
          })}
          {!visibleGuides.length && <div className="documentation-tree-empty">Tidak ada topik yang cocok.</div>}
        </nav>
      </aside>

      <article className="documentation-content">
        <section className="documentation-hero">
          <div className="documentation-hero-icon"><BookOpen size={28} /></div>
          <div><div className="documentation-kicker">Panduan Pengguna · Bahasa Indonesia</div><h1>Dokumentasi Dispatch Intelligence Platform</h1><p>Fungsi aplikasi, langkah penggunaan, cara membaca seluruh card, rumus, dan contoh perhitungan dalam satu panduan yang dapat dicari.</p></div>
        </section>

        {query.trim() && (
          <section className="documentation-search-results" aria-live="polite">
            <div><Search size={18} /><strong>Hasil pencarian “{query}”</strong></div>
            {searchResults.length ? <div className="documentation-result-links">{searchResults.map((result) => <button type="button" key={result.id} onClick={() => jumpTo(result.id)}>{result.title}<ChevronRight size={14} /></button>)}</div> : <p>Coba kata seperti confidence, pairing, cycle time, HHI, atau nama card.</p>}
          </section>
        )}

        {guides.map((guide) => (
          <section className="doc-section" id={guide.id} key={guide.id}>
            <div className="doc-section-heading">
              <div><span>{guide.number}</span><div><h2>{guide.title}</h2><p>{guide.description}</p></div></div>
              {guide.page && <button type="button" onClick={() => onNavigate(guide.page as AppPage)}>Buka halaman <ExternalLink size={15} /></button>}
            </div>
            {guide.topics.map((topic) => (
              <section className="doc-topic" id={topic.id} key={topic.id}>
                <h3><span>#</span>{topic.title}</h3>
                {topic.paragraphs?.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                {topic.steps && <ol className="doc-steps">{topic.steps.map((step, index) => <li key={step.title}><span>{index + 1}</span><div><strong>{step.title}</strong><p>{step.text}</p></div></li>)}</ol>}
                {topic.cards && <CardTable rows={topic.cards} />}
                {topic.formulas?.map((formula) => <FormulaBlock value={formula} key={formula} />)}
                {topic.examples?.map((example) => <ExampleBox example={example} key={example.title} />)}
                {topic.note && <NoteBox>{topic.note}</NoteBox>}
              </section>
            ))}
          </section>
        ))}

        <button type="button" className="documentation-back-to-top" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}><ArrowUp size={16} /> Kembali ke atas</button>
      </article>
    </div>
  );
}
