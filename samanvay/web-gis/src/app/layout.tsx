import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'GEOVAX — Enterprise Web GIS & Land Records Platform',
  description: 'National Geospatial Land Harmonisation System with RBAC/ABAC, CesiumJS 3D, and GeoAI',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link href="https://unpkg.com/maplibre-gl@3.3.1/dist/maplibre-gl.css" rel="stylesheet" />
        <link href="https://cesium.com/downloads/cesiumjs/releases/1.115/Build/Cesium/Widgets/widgets.css" rel="stylesheet" />
      </head>
      <body>{children}</body>
    </html>
  );
}
