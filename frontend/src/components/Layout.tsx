import React from "react";
import { Outlet } from "react-router-dom";
import { Navbar } from "./Navbar";

export const Layout: React.FC = () => {
  return (
    <div className="app-root">
      <Navbar />
      <main className="app-main">
        <div className="content-wrap">
          <Outlet />
        </div>
      </main>
    </div>
  );
};
