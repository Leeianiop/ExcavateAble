import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { JobDetail } from "./pages/JobDetail";
import { AssetLibrary } from "./pages/AssetLibrary";
import { Viewer } from "./pages/Viewer";
import { LiveStream } from "./pages/LiveStream";
import "./index.css";

const NotFound: React.FC = () => (
  <div style={{ padding: "4rem 2rem", textAlign: "center" }}>
    <h1 style={{ fontSize: "4rem", margin: 0 }}>404</h1>
    <p style={{ color: "#64748b", marginBottom: "2rem" }}>
      The page you are looking for doesn't exist.
    </p>
    <a href="/" className="btn btn-primary">
      ← Go Home
    </a>
  </div>
);

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/live" element={<LiveStream />} />
          <Route path="/jobs/:jobId" element={<JobDetail />} />
          <Route path="/assets" element={<AssetLibrary />} />
          <Route path="/viewer/:jobId" element={<Viewer />} />
          <Route path="/viewer" element={<Navigate to="/assets" replace />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
};

export default App;
