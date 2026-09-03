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
}

export const AVAILABLE_WARDS: WardLocation[] = [
  { id: "all", name: "All Wards & Zones (Chennai, Tambaram & Perungalathur)", taluk: "Entire Metropolitan Extent", center: [80.180, 12.980], zoom: 11.5, parcelCountApprox: 15800 },
  // South Chennai & Tambaram Corporation (Contiguous Cadastre)
  { id: "Perungalathur", name: "Zone 7 · Perungalathur (Kamaraj High Rd / GST)", taluk: "Tambaram Taluk", center: [80.094, 12.905], zoom: 15.2, parcelCountApprox: 1650 },
  { id: "Mudichur", name: "Zone 6 · Mudichur (Sriperumbudur Main Rd)", taluk: "Tambaram Taluk", center: [80.078, 12.912], zoom: 15.2, parcelCountApprox: 1450 },
  { id: "Tambaram", name: "Zone 1 · Tambaram Central (West & East GST)", taluk: "Tambaram Taluk", center: [80.118, 12.924], zoom: 15.0, parcelCountApprox: 2600 },
  { id: "Chromepet", name: "Zone 2 · Chromepet (MIT & Radha Nagar)", taluk: "Pallavaram Taluk", center: [80.142, 12.952], zoom: 15.0, parcelCountApprox: 2400 },
  { id: "Pallavaram", name: "Zone 3 · Pallavaram (Cantonment Axis)", taluk: "Pallavaram Taluk", center: [80.155, 12.968], zoom: 15.0, parcelCountApprox: 1950 },
  { id: "Hasthinapuram", name: "Zone 4 · Hasthinapuram", taluk: "Tambaram Taluk", center: [80.148, 12.946], zoom: 15.2, parcelCountApprox: 1420 },
  // Central & North Chennai
  { id: "Egmore", name: "Ward 104 · Egmore", taluk: "Egmore - Nungambakkam", center: [80.260, 13.080], zoom: 15.0, parcelCountApprox: 1840 },
  { id: "Chetpet", name: "Ward 105 · Chetpet", taluk: "Egmore - Nungambakkam", center: [80.238, 13.072], zoom: 15.2, parcelCountApprox: 2120 },
  { id: "Nungambakkam", name: "Ward 110 · Nungambakkam", taluk: "Egmore - Nungambakkam", center: [80.242, 13.058], zoom: 15.0, parcelCountApprox: 2450 },
  { id: "Mylapore", name: "Ward 120 · Mylapore", taluk: "Mylapore - Triplicane", center: [80.268, 13.036], zoom: 15.0, parcelCountApprox: 1980 },
  { id: "Chintadripet", name: "Ward 61 · Chintadripet", taluk: "Fort - Tondiarpet", center: [80.272, 13.075], zoom: 15.2, parcelCountApprox: 1250 },
  { id: "Periyamet", name: "Ward 58 · Periyamet", taluk: "Fort - Tondiarpet", center: [80.271, 13.084], zoom: 15.2, parcelCountApprox: 1410 },
  { id: "Puliyanthopu", name: "Ward 72 · Puliyanthopu", taluk: "Perambur - Purasawakkam", center: [80.265, 13.098], zoom: 15.0, parcelCountApprox: 1620 },
  { id: "Vepary", name: "Ward 57 · Vepery", taluk: "Perambur - Purasawakkam", center: [80.262, 13.088], zoom: 15.0, parcelCountApprox: 1180 },
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
    id: "usr-tambaram",
    name: "Tahsildar (Tambaram & Perungalathur)",
    role: "tahsildar",
    token: "token-tahsildar-tambaram",
    wardScope: ["Perungalathur", "Mudichur", "Tambaram", "Chromepet", "Pallavaram", "Hasthinapuram"],
    districtScope: "572",
    description: "Jurisdiction: Perungalathur, Mudichur, Tambaram",
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
    id: "usr-mylapore",
    name: "Tahsildar (Mylapore Div)",
    role: "tahsildar",
    token: "token-tahsildar-mylapore",
    wardScope: ["Mylapore", "Vivakanandapuram", "120", "112"],
    districtScope: "571",
    description: "Jurisdiction: Mylapore, Vivekanandapuram",
  },
  {
    id: "usr-citizen",
    name: "Public Citizen (Bhu-Darpan)",
    role: "citizen",
    token: "token-citizen",
    description: "Read-Only Citizen View",
  },
];
