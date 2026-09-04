export type UserRole = "super_admin" | "state_director" | "district_collector" | "tahsildar" | "survey_officer" | "citizen";

export interface UserProfile {
  id: string;
  name: string;
  role: UserRole;
  token: string;
  wardScope?: string[];
  districtScope?: string;
  description: string;
}

export interface WardLocation {
  id: string;
  name: string;
  taluk: string;
  center: [number, number]; // [lon, lat]
  zoom: number;
  parcelCountApprox: number;
  majorStreets?: string[];
}

export const AVAILABLE_WARDS: WardLocation[] = [
  {
    id: "all",
    name: "All Wards & Zones (Vandalur to Guindy Corridor)",
    taluk: "Metropolitan Extent",
    center: [80.145, 12.950],
    zoom: 11.5,
    parcelCountApprox: 24000,
    majorStreets: ["Grand Southern Trunk (GST) Road", "Outer Ring Road (ORR)", "Anna Salai", "Mount-Poonamallee Road", "Mudichur Road", "Gandhi Road"],
  },
  // Vandalur to Guindy Corridor
  {
    id: "Vandalur",
    name: "Zone 9 · Vandalur (Crescent & Zoo Junction)",
    taluk: "Vandalur Taluk",
    center: [80.082, 12.888],
    zoom: 15.3,
    parcelCountApprox: 1200,
    majorStreets: ["Vandalur Zoo Road", "Crescent College Road", "Otteri Main Road", "GST Road (Vandalur Junction)", "Vandalur-Kelambakkam Road"],
  },
  {
    id: "Old Perungalathur",
    name: "Zone 8 · Old Perungalathur (Sivan Koil & Srinivasa Nagar)",
    taluk: "Tambaram Taluk",
    center: [80.086, 12.898],
    zoom: 15.3,
    parcelCountApprox: 1200,
    majorStreets: ["Sivan Koil Street", "Srinivasa Nagar Main Road", "Old GST Road", "Kamaraj High Road South", "Gandhi Nagar 1st Street"],
  },
  {
    id: "New Perungalathur",
    name: "Zone 7 · New Perungalathur (Gandhi Rd & Peerkankaranai)",
    taluk: "Tambaram Taluk",
    center: [80.096, 12.908],
    zoom: 15.3,
    parcelCountApprox: 1400,
    majorStreets: ["Gandhi Road", "Kalaignar Street", "Peerkankaranai Main Road", "Lake View Street", "Bharathiyar Street"],
  },
  {
    id: "Mudichur",
    name: "Zone 6 · Mudichur (Sriperumbudur Main Rd & Eri)",
    taluk: "Tambaram Taluk",
    center: [80.078, 12.912],
    zoom: 15.2,
    parcelCountApprox: 1400,
    majorStreets: ["Mudichur-Sriperumbudur Main Road", "Veeralakshmi Nagar 1st Main Road", "Veeralakshmi Nagar Cross Street", "Attai Valavu Street", "Parvathy Nagar Main Road", "Mudichur Eri Bund Road", "Kamarajar Street"],
  },
  {
    id: "Veeralakshmi Nagar",
    name: "Zone 6A · Veeralakshmi Nagar (Mudichur)",
    taluk: "Tambaram Taluk",
    center: [80.0712, 12.9124],
    zoom: 16.2,
    parcelCountApprox: 1200,
    majorStreets: [
      "Veeralakshmi Nagar 1st Main Road",
      "Veeralakshmi Nagar 2nd Cross Street",
      "Veeralakshmi Nagar Extension",
      "Mudichur-Sriperumbudur Main Road",
      "Parvathy Nagar Main Road"
    ],
  },
  {
    id: "Tambaram",
    name: "Zone 1 · Tambaram Central (West & East GST)",
    taluk: "Tambaram Taluk",
    center: [80.118, 12.924],
    zoom: 15.0,
    parcelCountApprox: 2600,
    majorStreets: ["Shanmugam Road", "Gandhi Road (West Tambaram)", "Kakkan Street", "Rajaji Road", "Selaiyur Camp Road"],
  },
  {
    id: "Tambaram Sanatorium",
    name: "Zone 1A · Tambaram Sanatorium (MEPZ Corridor)",
    taluk: "Tambaram Taluk",
    center: [80.130, 12.938],
    zoom: 15.2,
    parcelCountApprox: 1200,
    majorStreets: ["MEPZ Main Avenue", "TB Hospital Road", "National Institute of Siddha Road", "Sanatorium Station Road"],
  },
  {
    id: "Chromepet",
    name: "Zone 2 · Chromepet (MIT & Radha Nagar)",
    taluk: "Pallavaram Taluk",
    center: [80.142, 12.952],
    zoom: 15.0,
    parcelCountApprox: 2400,
    majorStreets: ["MIT Road", "Radha Nagar Main Road", "CLRI Nagar", "Station Road", "Kumaran Street"],
  },
  {
    id: "Pallavaram",
    name: "Zone 3 · Pallavaram (Cantonment & Old Trunk)",
    taluk: "Pallavaram Taluk",
    center: [80.155, 12.968],
    zoom: 15.0,
    parcelCountApprox: 1950,
    majorStreets: ["Cantonment Road", "Pammal Main Road", "Old Trunk Road", "Bazaar Street"],
  },
  {
    id: "Hasthinapuram",
    name: "Zone 4 · Hasthinapuram",
    taluk: "Tambaram Taluk",
    center: [80.148, 12.946],
    zoom: 15.2,
    parcelCountApprox: 1420,
    majorStreets: ["Hasthinapuram Main Road", "Gayathri Nagar 1st Cross", "Senthil Nagar"],
  },
  {
    id: "Tirusulam",
    name: "Zone 5 · Tirusulam & Chennai Airport (MAA)",
    taluk: "Pallavaram Taluk",
    center: [80.165, 12.980],
    zoom: 15.2,
    parcelCountApprox: 1000,
    majorStreets: ["Airport Flyover Road", "Tirusulam Hill Road", "Old Airport Road"],
  },
  {
    id: "Meenambakkam",
    name: "Zone 5A · Meenambakkam (Civil Aviation)",
    taluk: "Alandur Taluk",
    center: [80.176, 12.992],
    zoom: 15.2,
    parcelCountApprox: 1000,
    majorStreets: ["Civil Aviation Colony Road", "Cargo Complex Road", "Meenambakkam Station Road"],
  },
  {
    id: "Alandur",
    name: "Zone 5B · Alandur (MKN Road & Metro)",
    taluk: "Alandur Taluk",
    center: [80.190, 13.004],
    zoom: 15.2,
    parcelCountApprox: 1200,
    majorStreets: ["MKN Road", "Alandur Metro Station Road", "Asarhana Street", "Cement Road"],
  },
  {
    id: "Guindy",
    name: "Zone 5C · Guindy (Kathipara & Industrial Estate)",
    taluk: "Guindy Taluk",
    center: [80.208, 13.010],
    zoom: 15.0,
    parcelCountApprox: 1400,
    majorStreets: ["Kathipara Junction", "Guindy Industrial Estate Road", "Race Course Road", "Mount-Poonamallee Road", "Anna Salai (Guindy End)"],
  },
  // Central Chennai
  {
    id: "Egmore",
    name: "Ward 104 · Egmore",
    taluk: "Egmore - Nungambakkam",
    center: [80.260, 13.080],
    zoom: 15.0,
    parcelCountApprox: 1840,
    majorStreets: ["Gandhi Irwin Road", "Poonamallee High Road", "Whannels Road", "Ritherdon Road"],
  },
  {
    id: "Chetpet",
    name: "Ward 105 · Chetpet",
    taluk: "Egmore - Nungambakkam",
    center: [80.238, 13.072],
    zoom: 15.2,
    parcelCountApprox: 2120,
    majorStreets: ["McNichols Road", "Spur Tank Road", "Harrington Road"],
  },
  {
    id: "Nungambakkam",
    name: "Ward 110 · Nungambakkam",
    taluk: "Egmore - Nungambakkam",
    center: [80.242, 13.058],
    zoom: 15.0,
    parcelCountApprox: 2450,
    majorStreets: ["Nungambakkam High Road", "College Road", "Kothari Road"],
  },
  {
    id: "Mylapore",
    name: "Ward 120 · Mylapore",
    taluk: "Mylapore - Triplicane",
    center: [80.268, 13.036],
    zoom: 15.0,
    parcelCountApprox: 1980,
    majorStreets: ["Luz Church Road", "Kutchery Road", "R.K. Mutt Road"],
  },
  // Pan-India Synthesized Nodes
  {
    id: "Delhi",
    name: "NCT Delhi (Connaught Place)",
    taluk: "New Delhi",
    center: [77.216, 28.632],
    zoom: 15.0,
    parcelCountApprox: 8500,
    majorStreets: ["Connaught Circle", "Parliament Street", "Barakhamba Road"],
  },
  {
    id: "Mumbai",
    name: "Maharashtra (Nariman Point)",
    taluk: "Mumbai City",
    center: [72.823, 18.925],
    zoom: 15.0,
    parcelCountApprox: 12000,
    majorStreets: ["Marine Drive", "Madam Cama Road", "Veer Nariman Road"],
  },
  {
    id: "Bengaluru",
    name: "Karnataka (Vidhana Soudha)",
    taluk: "Bengaluru Urban",
    center: [77.594, 12.979],
    zoom: 15.0,
    parcelCountApprox: 9800,
    majorStreets: ["Ambedkar Veedhi", "Cubbon Park Road", "Raj Bhavan Road"],
  },
  {
    id: "Anna Salai",
    name: "Zone 1 · Anna Salai (Actual Data)",
    taluk: "Mylapore - Triplicane",
    center: [80.256, 13.054],
    zoom: 16.5,
    parcelCountApprox: 15000,
    majorStreets: ["Anna Salai", "Walajah Road", "Bells Road"],
  }
];

export const PRESET_USERS: UserProfile[] = [
  {
    id: "usr-super",
    name: "National Director (NIC)",
    role: "super_admin",
    token: "token-superadmin",
    description: "Pan-India Access (SuperAdmin)",
  },
  {
    id: "usr-corridor",
    name: "Tahsildar (Vandalur – Guindy Corridor & Chennai)",
    role: "tahsildar",
    token: "token-tahsildar-tambaram",
    wardScope: ["Vandalur", "Old Perungalathur", "New Perungalathur", "Mudichur", "Tambaram", "Tambaram Sanatorium", "Chromepet", "Pallavaram", "Hasthinapuram", "Tirusulam", "Meenambakkam", "Alandur", "Guindy", "Anna Salai"],
    districtScope: "572",
    description: "Jurisdiction: Vandalur to Guindy GST Corridor",
  },
  {
    id: "usr-egmore",
    name: "Tahsildar (Egmore Div)",
    role: "tahsildar",
    token: "token-tahsildar-egmore",
    wardScope: ["Egmore", "Chetpet", "Nungambakkam", "104", "105", "110"],
    districtScope: "571",
    description: "Jurisdiction: Egmore, Chetpet, Nungambakkam",
  },
  {
    id: "usr-citizen",
    name: "Public Citizen (Bhu-Darpan)",
    role: "citizen",
    token: "token-citizen",
    description: "Read-Only Citizen View",
  },
];
