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

export const PRESET_USERS: UserProfile[] = [
  {
    id: "usr-super",
    name: "National Director (NIC)",
    role: "super_admin",
    token: "token-superadmin",
    description: "Pan-India Access (SuperAdmin)",
  },
  {
    id: "usr-egmore",
    name: "Tahsildar (Egmore & Kilpauk)",
    role: "tahsildar",
    token: "token-tahsildar-egmore",
    wardScope: ["104", "105", "106", "Egmore", "Kilpauk"],
    districtScope: "571",
    description: "Restricted to Wards 104-106 (Egmore Div)",
  },
  {
    id: "usr-mylapore",
    name: "Tahsildar (Mylapore & Alwarpet)",
    role: "tahsildar",
    token: "token-tahsildar-mylapore",
    wardScope: ["120", "121", "122", "Mylapore", "Alwarpet"],
    districtScope: "571",
    description: "Restricted to Wards 120-122 (Mylapore Div)",
  },
  {
    id: "usr-citizen",
    name: "Public Citizen (Bhu-Darpan)",
    role: "citizen",
    token: "token-citizen",
    description: "Read-Only Verified Records",
  },
];
